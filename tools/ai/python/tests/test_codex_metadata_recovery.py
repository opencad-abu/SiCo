"""Optional metadata failure cannot poison the next independently submitted task."""

import json
import os
import select
from collections import deque
from threading import Event
from types import SimpleNamespace

import pytest
from codex_rpc_fixture import pipes  # noqa: F401
from codex_thread_fixture import bind_thread
from sico.codex import runtime_assembly
from sico.codex.backend import CodexBackend
from sico.codex.rpc import RpcError
from sico.core.tools import ToolRegistry
from sico.storage.journal import SessionJournal
from test_agent_session_names import CONTEXT, LABEL


@pytest.fixture
def backend(tmp_path):
    with SessionJournal(tmp_path, "metadata-recovery") as journal:
        loop = CodexBackend(SimpleNamespace(label=LABEL), ToolRegistry(), journal, CONTEXT)
        loop.resources.prepare()
        bind_thread(loop, "thread-a")
        try:
            yield loop
        finally:
            loop.close()


def completed(turn):
    return [
        {"method": "item/completed", "params": {
            "threadId": "thread-a", "turnId": turn,
            "item": {"type": "agentMessage", "id": turn + "-answer", "text": "Done."}}},
        {"method": "turn/completed", "params": {
            "threadId": "thread-a", "turnId": turn,
            "turn": {"id": turn, "status": "completed"}}},
    ]


class ResumedRpc:
    def __init__(self):
        self.calls, self.frames = [], deque()

    def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method in {"thread/resume", "thread/read"}:
            return {"thread": {"id": "thread-a", "name": "Existing",
                               "historyMode": "legacy", "turns": []}}
        if method == "turn/start":
            self.frames.extend(completed("next-turn"))
            return {"turn": {"id": "next-turn"}}
        return {}

    def next(self):
        return self.frames.popleft()


class PartialHistoryRpc(ResumedRpc):
    def __init__(self):
        super().__init__()
        self.stopped = Event()
        self.closed = False

    def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method == "thread/resume":
            return {"thread": {"id": "thread-a", "historyMode": "paginated", "turns": []}}
        if method == "thread/turns/list":
            self.stopped.set()
            raise RpcError("history page timed out", code="timeout", sent_bytes=1)
        return {}

    def close(self):
        self.closed = True


def test_history_paging_timeout_rebuilds_without_replacing_the_task(
        backend, monkeypatch):
    first, second = PartialHistoryRpc(), ResumedRpc()
    runtimes = iter([
        SimpleNamespace(rpc=first, cwd="/tmp", close=first.close),
        SimpleNamespace(rpc=second, cwd="/tmp", close=lambda: None),
    ])
    monkeypatch.setattr(runtime_assembly, "CodexRuntime",
                        lambda *args, **kwargs: next(runtimes))
    monkeypatch.setattr(runtime_assembly, "ToolBridge",
                        lambda *args, **kwargs: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(backend.resources, "register", lambda rpc: None)
    monkeypatch.setattr(backend.thread_ops.goals, "before_resume", lambda rpc: None)
    monkeypatch.setattr(backend.thread_ops.memory, "apply", lambda rpc: None)

    events = list(backend.run("Retry without history", context=CONTEXT,
                              input_id="retry-history", turn_options={}))

    assert events[-1]["kind"] == "task.completed"
    assert not any(event["kind"] == "task.needs_reconcile" for event in events)
    assert any(event["kind"] == "codex.history.unavailable" for event in events)
    assert first.closed and first.stopped.is_set()
    assert [name for name, _ in second.calls].count("thread/resume") == 1
    starts = [params for name, params in second.calls if name == "turn/start"]
    assert len(starts) == 1 and starts[0]["clientUserMessageId"] == "retry-history"
    assert backend.native_history.disabled


@pytest.mark.parametrize("method", ["thread/read", "thread/name/set"])
def test_metadata_timeout_disposes_connection_and_next_input_resumes_once(
        backend, pipes, monkeypatch, method):  # noqa: F811
    rpc, peer, child, start = pipes
    original = rpc.request
    observed = []

    def short_metadata(name, params, timeout=30):
        return original(name, params, timeout=.1)

    def peer_reads():
        while True:
            assert select.select([peer], [], [], 2)[0]
            request = json.loads(os.read(peer, 65536))
            observed.append(request)
            if request["method"] == method:
                return  # Keep the peer alive without acknowledging this RPC.
            os.write(child, (json.dumps({"id": request["id"], "result": {
                "thread": {"id": "thread-a", "name": None, "preview": "Ask test"}
            }}) + "\n").encode())

    monkeypatch.setattr(rpc, "request", short_metadata)
    worker = start(peer_reads)
    backend.runtime = SimpleNamespace(rpc=rpc, close=rpc.close)
    backend._begin("First Ask", CONTEXT, "first", "chat")
    os.write(child, b"".join((json.dumps(frame) + "\n").encode()
                            for frame in completed("first-turn")))
    first = list(backend._turn("first-turn", lambda: False))
    worker.join(2)
    assert not worker.is_alive()
    assert first[-1]["kind"] == "task.completed"
    assert backend.state.task["status"] == "completed"
    assert observed[-1]["method"] == method
    assert rpc.stopped.is_set()
    assert backend.runtime is None, "Stopped metadata connection survived task completion"
    assert rpc._closed and not rpc.reader.is_alive()
    assert backend.thread_id == "thread-a"

    resumed = ResumedRpc()
    monkeypatch.setattr(runtime_assembly, "CodexRuntime", lambda *args, **kwargs:
                        SimpleNamespace(rpc=resumed, cwd="/tmp", close=lambda: None))
    monkeypatch.setattr(runtime_assembly, "ToolBridge", lambda *args, **kwargs:
                        SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(backend.resources, "register", lambda rpc: None)
    monkeypatch.setattr(backend.thread_ops.goals, "before_resume", lambda rpc: None)
    monkeypatch.setattr(backend.thread_ops.memory, "apply", lambda rpc: None)
    second = list(backend.run("Second Ask", context=CONTEXT, input_id="second", turn_options={}))
    assert second[-1]["kind"] == "task.completed", backend.state.task.get("diagnostic")
    assert resumed.calls[0][0] == "thread/resume"
    assert resumed.calls[0][1]["threadId"] == "thread-a"
    starts = [params for name, params in resumed.calls if name == "turn/start"]
    assert len(starts) == 1 and starts[0]["clientUserMessageId"] == "second"
    assert starts[0]["input"][0]["text"] == "Second Ask"
    assert not any(name == "thread/start" for name, _ in resumed.calls)
    assert not any(event["kind"] == "task.needs_reconcile" for event in backend.journal.events())
    backend.close()


def test_timeout_origin_survives_later_unsent_request(pipes):  # noqa: F811
    rpc, peer, _, _ = pipes
    with pytest.raises(RpcError) as first:
        rpc.request("thread/read", {"threadId": "thread-a"}, timeout=.05)
    assert first.value.code == "timeout" and first.value.sent_bytes > 0
    assert json.loads(os.read(peer, 65536))["method"] == "thread/read"
    with pytest.raises(RpcError) as second:
        rpc.request("turn/start", {})
    assert second.value.phase == "request_slot" and second.value.sent_bytes == 0
    assert second.value.code == "timeout", "A local timeout was misreported as a process exit"
    assert "process still exiting" not in str(second.value)
    assert not select.select([peer], [], [], 0)[0]
    with pytest.raises(RpcError) as read:
        rpc._next(.01)
    assert read.value.code == "timeout"


def test_healthy_metadata_keeps_connection(backend):
    rpc, closed = ResumedRpc(), []
    backend.runtime = SimpleNamespace(rpc=rpc, close=lambda: closed.append(True))
    backend._begin("Ask test", CONTEXT, "first", "chat")
    rpc.frames.extend(completed("first-turn"))
    events = list(backend._turn("first-turn", lambda: False))
    assert events[-1]["kind"] == "task.completed"
    assert backend.runtime.rpc is rpc and not closed


def test_metadata_cleanup_failure_is_not_hidden(backend):
    rpc = ResumedRpc()
    rpc.stopped = Event()

    def fail_metadata(*args, **kwargs):
        rpc.stopped.set()
        raise RpcError("metadata timeout", code="timeout")

    def fail_cleanup():
        raise RpcError("cleanup unconfirmed", code="cleanup_unconfirmed")

    rpc.request = fail_metadata
    backend.runtime = SimpleNamespace(rpc=rpc, close=fail_cleanup)
    backend._begin("Ask test", CONTEXT, "first", "chat")
    rpc.frames.extend(completed("first-turn"))
    with pytest.raises(RpcError, match="cleanup unconfirmed"):
        list(backend._turn("first-turn", lambda: False))
    assert not any(event["kind"] == "task.completed" for event in backend.journal.events())
    backend.runtime.close = lambda: None
    backend.close()


def test_local_response_handler_failure_is_not_reported_as_process_exit(pipes):  # noqa: F811
    rpc, peer, child, start = pipes

    def fail_notification(event):
        raise OSError("private-handler-sentinel")

    def server():
        json.loads(os.read(peer, 65536))
        os.write(child, b'{"method":"thread/name/updated"}\n')

    rpc.notification_handler = fail_notification
    start(server)
    with pytest.raises(OSError):
        rpc.request("thread/read", {})
    with pytest.raises(RpcError) as caught:
        rpc.request("turn/start", {})
    assert caught.value.code == "unexpected_exception"
    assert caught.value.sent_bytes == 0
    assert "private-handler-sentinel" not in str(caught.value)
    assert "private-handler-sentinel" not in rpc._diagnostic.path.read_text()
