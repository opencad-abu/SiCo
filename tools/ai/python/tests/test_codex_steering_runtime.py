"""turn/steer through original 0.154 and a controlled Responses endpoint."""

from sico.service.frontend_session import SessionToken

import json
import threading
import time

import pytest
from agent_sessions_harness import DesktopSessions
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    answer,
    gateway,
    native_codex,
    registry,
    settings,
)

from sico.codex.backend import CodexBackend
from sico.codex.rpc import AppServer, RpcRejected
from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal


def wait_for(condition, timeout=8):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "Native turn did not reach expected state"
        time.sleep(0.01)


@pytest.mark.parametrize("tool_format", ["native", "flat"])
def test_actual_steer_keeps_task_thread_turn_and_queue(
    tmp_path, gateway, native_codex, tool_format, monkeypatch,  # noqa: F811
):
    entered, release = threading.Event(), threading.Event()
    calls = []
    original = AppServer.request

    def request(self, method, params, **kwargs):
        calls.append((method, params, threading.current_thread().name))
        return original(self, method, params, **kwargs)

    def first(_request):
        entered.set()
        assert release.wait(10)
        return answer("Initial reading complete.")

    monkeypatch.setattr(AppServer, "request", request)
    gateway.responses.put(first)
    gateway.responses.put(answer("Applied the supplement to this task."))
    provider = settings(gateway, native_codex, tool_format)
    with SessionJournal(tmp_path, "steer") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        controller = SessionController(backend)
        sessions = DesktopSessions(controller)
        try:
            controller.submit("Inspect the captured target.")
            assert entered.wait(8)
            wait_for(lambda: bool(controller.updates.read_updates()["snapshot"]["steering"]))
            scope = controller.updates.read_updates()["snapshot"]["steering"]
            # Queued tasks remain a distinct path; this supplement must not add a queue row.
            queue_before = controller.history.snapshot()
            result = sessions.worker.command(SessionToken(controller.session_id, controller.runtime_id), "steer", "Use revised limit 17.",
                scope=scope, request_id="supplement_one").result(8)
            assert result["status"] == "accepted", result
            assert result["scope"]["turn_id"] == scope["turn_id"]
            assert controller.history.snapshot() == queue_before
            assert not controller.inbox.pending
            release.set()
            wait_for(lambda: not controller.busy)
            assert backend.state.task["status"] == "completed", backend.state.task
            assert len(gateway.requests) == 2
            assert "Use revised limit 17." in json.dumps(gateway.requests[-1])
            turns = backend.runtime.rpc.request("thread/read", {
                "threadId": scope["thread_id"], "includeTurns": True,
            })["thread"]["turns"]
            assert len(turns) == 1 and turns[0]["id"] == scope["turn_id"]
            assert "Use revised limit 17." in json.dumps(turns)
            assert len([e for e in journal.events() if e["kind"] == "task.started"]) == 1
            steer_calls = [c for c in calls if c[0] == "turn/steer"]
            assert len(steer_calls) == 1
            assert steer_calls[0][1]["expectedTurnId"] == scope["turn_id"]
            assert steer_calls[0][2].startswith("copilot-session-")
            assert len([c for c in calls if c[0] == "turn/start"]) == 1
            assert not controller.updates.read_updates()["snapshot"]["steering"]
            with pytest.raises(ValueError, match="回合已变化"):
                controller.backend_commands.steer("Late edit", scope=scope, request_id="late_edit")
            # Confirm the original app-server rejects an ended expected turn;
            # the production controller above rejects it before making any RPC.
            with pytest.raises(RpcRejected):
                backend.runtime.rpc.request("turn/steer", {
                    "threadId": scope["thread_id"], "expectedTurnId": scope["turn_id"],
                    "input": [{"type": "text", "text": "Do not start another task."}],
                })
            (tmp_path / "steering-proof.json").write_text(json.dumps({
                "scope": scope, "receipt": result, "turns": turns,
                "rpc_methods": [c[0] for c in calls],
            }, ensure_ascii=False, indent=2))
        finally:
            release.set()
            sessions.close()
            assert sessions.wait(8)
