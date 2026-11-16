"""Late MCP replies must not retire a desktop or cross into its next task."""

import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from sico.codex.backend import CodexBackend
from sico.codex.mcp import ToolBridge
from sico.core.contracts import BoundContext, ToolResult
from sico.core.tools import Tool, ToolRegistry, no_arguments
from sico.demo import DemoPeer
from sico.core.session_events import SESSION_CONTRACT, EventCursor
from sico.storage.journal import SessionJournal
from sico.storage.workbench import WorkbenchIndex
from sico.transport.bridge_client import BridgeClient
from sico.transport.broker import _CALL_CONTEXT, skill_call_context
from sico.transport.framing import Connection
from sico.transport.instance_bridge import InstanceBridge
from sico.transport.methods import QueryUnavailable

CONTEXT = BoundContext("instance", "generation", "target", {"source": "simulated"})


def batch(events):
    return dict(contract=SESSION_CONTRACT, session_id="session", runtime_id="runtime",
                instance_id="instance", generation="generation", events=events)


def backend_for(journal, handler):
    tools = ToolRegistry()
    tools.register(Tool("collect", "采集器件", {"type": "object"}, no_arguments, handler))
    backend = CodexBackend(SimpleNamespace(), tools, journal, CONTEXT)
    from codex_thread_fixture import bind_thread
    bind_thread(backend)
    backend._begin("我想设计一个LDO", CONTEXT, "input", "chat")
    backend.active, backend.cancelled = True, lambda: False
    return backend


def progress(state="completed"):
    fields = _CALL_CONTEXT.get()
    fields["progress"](dict(
        instance_id="instance", generation="generation", target_id="target",
        session_id="session", task_id=fields["task_id"], tool_call_id=fields["tool_call_id"],
        request_id="a" * 32, state=state,
    ))


@pytest.mark.parametrize("late", [False, True])
def test_native_turn_end_with_running_mcp_tool_seals_receipts(tmp_path, late):
    entered, release = threading.Event(), threading.Event()
    stopped = []

    def collect(_args, _context):
        progress("running")
        entered.set()
        assert release.wait(5)
        stopped.append(_CALL_CONTEXT.get()["cancelled"]())
        progress()
        return ToolResult("ok", "采集完成", {"devices": 182})

    with SessionJournal(tmp_path, "session") as journal, ThreadPoolExecutor(1) as pool:
        backend = backend_for(journal, collect)
        bridge = ToolBridge(backend.tools, backend._execute)
        backend.bridge = bridge

        def call():
            with socket.create_connection(("127.0.0.1", bridge.port), timeout=5) as sock:
                connection = Connection(sock)
                connection.send(dict(token=bridge.token, method="call", name="collect", arguments={}))
                return connection.receive()

        result = pool.submit(call)
        try:
            assert entered.wait(2)
            backend.runtime = SimpleNamespace(close=lambda: None, rpc=SimpleNamespace(
                next=lambda: {"method": "turn/completed", "params": {
                    "threadId": "thread", "turnId": "turn",
                    "turn": {"id": "turn", "status": "interrupted"},
                }},
            ))
            if not late:
                def finish_tool():
                    assert backend._tool_stop.wait(2)
                    release.set()
                finisher = threading.Thread(target=finish_tool)
                finisher.start()
            events = list(backend._turn("turn", lambda: False))
            if not late:
                finisher.join(2)
            assert events[-1]["kind"] == ("task.needs_reconcile" if late else "task.cancelled")
            terminal_sequence = events[-1]["sequence"]
            if late:
                with pytest.raises(ValueError, match="收尾"):
                    backend.acknowledge_interrupted()
                assert backend.state.task["status"] == "needs_reconcile"
            release.set()
            assert not result.result(3)["result"]["isError"]
            assert stopped == [True]
            assert not backend._inflight
            rows = journal.events()
            assert all(e["kind"] == "session.tool_receipt"
                       for e in rows if e["sequence"] > terminal_sequence)
            cursor = EventCursor("session", "runtime", CONTEXT)
            cursor.consume(batch(rows))
            data = WorkbenchIndex(journal).sync().data.values()
            assert any(row.get("tool") == "collect" and row.get("status") == "ok"
                       and row["value"]["data"] == {"devices": 182} for row in data)
            if late:
                backend.acknowledge_interrupted()
            backend._begin("继续核对", CONTEXT, "next", "chat")
            backend.active = True
            backend._finish("completed")
            cursor.consume(batch(journal.events()[cursor.sequence:]))
        finally:
            release.set()
            result.result(5)
            backend.close()


def old_events():
    def event(kind, payload):
        return dict(kind=kind, payload=payload, task_id="task", session_id="session")
    rows = [
        event("task.started", dict(context=CONTEXT.record())),
        event("tool.started", dict(id="call", name="collect", input={})),
        event("task.cancelled", dict(message="Generation stopped; no external job was stopped")),
        event("router.status", dict(session_id="session", task_id="task", tool_call_id="call",
                                    instance_id="instance", generation="generation", state="completed")),
        event("model.status", dict(phase="tool_call", tool="collect")),
        event("tool.finished", dict(id="call", name="collect", result=ToolResult("ok").record())),
        event("model.status", dict(phase="waiting_model")),
    ]
    return [dict(row, sequence=i + 1) for i, row in enumerate(rows)]


def test_legacy_cancelled_history_replays_receipts_without_restarting_activity():
    rows = old_events()
    cursor = EventCursor("session", "runtime", CONTEXT, history_sequence=len(rows))
    received = []
    for row in rows:
        received.extend(cursor.consume(batch([row])))
    assert cursor.sequence == len(rows) and cursor.terminal
    assert [e["kind"] for e in received][-2:] == ["router.status", "tool.finished"]
    assert not any(e["kind"] == "model.status" for e in received)


@pytest.mark.parametrize("fault", ["live", "task", "generation", "call", "name", "duplicate", "native"])
def test_history_compatibility_keeps_identity_order_and_pairing_checks(fault):
    rows = old_events()
    if fault == "task": rows[3]["task_id"] = "other"
    if fault == "generation": rows[3]["payload"]["generation"] = "other"
    if fault == "call": rows[3]["payload"]["tool_call_id"] = "other"
    if fault == "name": rows[5]["payload"]["name"] = "other"
    if fault == "duplicate": rows.append(dict(rows[5], sequence=8))
    if fault == "native": rows[4]["kind"] = "codex.diff.updated"
    cursor = EventCursor("session", "runtime", CONTEXT,
                         history_sequence=0 if fault == "live" else len(rows))
    with pytest.raises(ValueError, match="Stale task"):
        cursor.consume(batch(rows))
    assert cursor.sequence == 0 and not cursor.pending_calls


def test_cancelled_tool_cannot_start_another_host_call(tmp_path):
    with InstanceBridge(tmp_path, CONTEXT) as bridge:
        peer = DemoPeer(bridge.broker, CONTEXT)
        try:
            with BridgeClient.discover(tmp_path, CONTEXT) as client:
                client.register_session("session", CONTEXT)
                with skill_call_context(session_id="session", cancelled=lambda: True):
                    for broker in (client, bridge.broker):
                        with pytest.raises(QueryUnavailable) as exc:
                            broker.get_context(CONTEXT)
                        assert exc.value.code == "cancelled_before_start"
                    assert "session" in client.bridge_snapshot()["sessions"]
        finally:
            peer.close()


def test_serialized_tool_cancelled_while_waiting_never_enters_handler(tmp_path):
    entered, release, requested = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def collect(_args, _context):
        calls.append(True)
        entered.set()
        assert release.wait(3)
        return ToolResult()

    with SessionJournal(tmp_path, "session") as journal, ThreadPoolExecutor(2) as pool:
        backend = backend_for(journal, collect)
        first = pool.submit(backend._execute, "collect", {})
        assert entered.wait(2)
        original = backend.tools.execute

        def execute(*args):
            requested.set()
            return original(*args)

        backend.tools.execute = execute
        second = pool.submit(backend._execute, "collect", {})
        try:
            assert requested.wait(2)
            backend._tool_stop.set()
        finally:
            release.set()
        first.result(3)
        reply = second.result(3)
        assert json.loads(reply["content"][0]["text"])["status"] == "cancelled_before_start"
        assert calls == [True]
        backend._finish("cancelled")
        EventCursor("session", "runtime", CONTEXT).consume(batch(journal.events()))
