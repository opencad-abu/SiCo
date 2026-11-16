"""A stopped idle connection is retired before the next independent input."""

from threading import Event
from types import SimpleNamespace

import pytest
from codex_rpc_fixture import pipes  # noqa: F401
from sico.codex import runtime_assembly
from sico.codex.rpc import RpcError
from sico.codex.runtime_lifecycle import authorize_new_task_recovery, ensure_runtime
from test_agent_session_names import CONTEXT
from test_codex_metadata_recovery import ResumedRpc, backend, completed  # noqa: F401


@pytest.fixture
def recovery(backend, monkeypatch):  # noqa: F811
    resumed, assembled = ResumedRpc(), []

    def runtime(*args, **kwargs):
        assembled.append(True)
        return SimpleNamespace(rpc=resumed, cwd="/tmp", close=lambda: None)

    monkeypatch.setattr(runtime_assembly, "CodexRuntime", runtime)
    monkeypatch.setattr(runtime_assembly, "ToolBridge", lambda *args, **kwargs:
                        SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(backend.resources, "register", lambda rpc: None)
    monkeypatch.setattr(backend.thread_ops.goals, "before_resume", lambda rpc: None)
    monkeypatch.setattr(backend.thread_ops.memory, "apply", lambda rpc: None)
    return resumed, assembled


def idle_task(session):
    session._begin("First Ask", CONTEXT, "first", "chat")
    session._finish("completed")
    list(session._drain())


@pytest.mark.parametrize("poll", [False, True])
def test_idle_disconnect_resumes_original_thread_once(backend, recovery, pipes, poll):  # noqa: F811
    rpc, _, _, _ = pipes
    backend.runtime = SimpleNamespace(rpc=rpc, close=rpc.close)
    idle_task(backend)
    # A stopped transport can retain a live child while its teardown is pending.
    rpc.stopped.set()
    resumed, assembled = recovery
    if poll:
        backend.poll_thread_names()
        assert backend.runtime is None and not assembled
    events = list(backend.run("Second Ask", input_id="second", turn_options={}))
    assert events[-1]["kind"] == "task.completed", backend.state.task.get("diagnostic")
    assert rpc._closed and not rpc.reader.is_alive()
    assert assembled == [True]
    assert resumed.calls[0] == ("thread/resume", {"threadId": "thread-a",
                                               **resumed.calls[0][1]})
    starts = [p for m, p in resumed.calls if m == "turn/start"]
    assert len(starts) == 1 and starts[0]["clientUserMessageId"] == "second"
    assert not any(m == "thread/start" for m, _ in resumed.calls)
    assert not any(e["kind"] == "task.needs_reconcile" for e in backend.journal.events())


def test_recovery_preserves_new_task_execution_permission(backend, recovery, pipes):  # noqa: F811
    rpc, _, _, _ = pipes
    backend.runtime = SimpleNamespace(rpc=rpc, close=rpc.close)
    idle_task(backend)
    rpc.stopped.set()
    events = list(backend.run("Second Ask", input_id="second", turn_options={}))
    assert events[-1]["kind"] == "task.completed"
    assert backend.active is False
    assert recovery[1] == [True]


def test_disconnect_between_task_admission_and_first_rpc_is_recovered_once(
        backend, recovery, pipes):  # noqa: F811
    rpc, _, _, _ = pipes
    backend.runtime = SimpleNamespace(rpc=rpc, close=rpc.close)
    idle_task(backend)
    authorized = authorize_new_task_recovery(backend)
    backend._begin("Second Ask", CONTEXT, "second", "chat")
    backend.active = True
    rpc.stopped.set()
    ensure_runtime(backend, recover_stopped=authorized)
    assert backend.runtime is not None and recovery[1] == [True]
    assert rpc._closed
    backend.active = False
    backend._finish("cancelled")
    backend.close()


@pytest.mark.parametrize("operation", ["name", "inputs", "thread", "resources"])
def test_idle_metadata_timeout_retires_without_replaying(backend, pipes, monkeypatch, operation):  # noqa: F811
    rpc, _, _, _ = pipes
    original = rpc.request
    calls = []

    def timeout(method, params, **kwargs):
        calls.append(method)
        return original(method, params, timeout=.02)

    monkeypatch.setattr(rpc, "request", timeout)
    backend.runtime = SimpleNamespace(rpc=rpc, cwd="/tmp", close=rpc.close)
    idle_task(backend)
    if operation == "resources":
        assert backend.resources.status()["status"] == "partial"
    else:
        with pytest.raises(RpcError):
            {"name": lambda: backend.set_thread_name("Manual name"),
             "inputs": backend.turn_options.status,
             "thread": backend.thread_ops.status}[operation]()
    assert len(calls) == 1
    assert backend.runtime is None, "Stopped metadata RPC survived its owner boundary"
    assert rpc._closed and not rpc.reader.is_alive()
    assert backend.state.task["status"] == "completed"


@pytest.mark.parametrize("blocked", ["active", "review", "reconcile", "tool"])
def test_idle_poll_does_not_touch_unsettled_task(backend, recovery, blocked):  # noqa: F811
    backend._begin("Ask", CONTEXT, "first", "chat")
    if blocked == "reconcile":
        backend._finish("needs_reconcile", "unknown request")
    elif blocked == "tool":
        backend._finish("completed")
        backend._inflight.add("finishing-tool")
    elif blocked == "review":
        backend.state.task["waiting_audits"] = ["review-a"]
    else:
        backend.active = True
    rpc, calls = ResumedRpc(), []
    rpc.stopped = Event()
    rpc.stopped.set()
    rpc.poll_notifications = lambda: calls.append("poll")
    rpc.poll_requests = lambda handler: calls.append("poll")
    runtime = SimpleNamespace(rpc=rpc, close=lambda: calls.append("close"))
    backend.runtime = runtime
    backend.poll_thread_names()
    assert not calls and backend.runtime is runtime and not recovery[1]
    backend._inflight.clear()
    backend._finish("cancelled")


def test_partial_cleanup_is_retained_and_cannot_be_bypassed(backend, recovery):  # noqa: F811
    idle_task(backend)
    attempts = []

    def fail_close():
        attempts.append(True)
        raise RpcError("cleanup unconfirmed", code="cleanup_unconfirmed")

    # RPC cleanup succeeded, but another owned resource has not acknowledged close.
    runtime = SimpleNamespace(rpc=None, close=fail_close)
    bridge = SimpleNamespace(close=fail_close)
    backend.runtime, backend.bridge = runtime, bridge
    recovery[0].request = lambda *args, **kwargs: pytest.fail("must not assemble runtime")
    try:
        with pytest.raises(RpcError, match="cleanup unconfirmed"):
            backend.close()
        assert backend.runtime is runtime and backend.bridge is bridge
        with pytest.raises(RpcError, match="cleanup unconfirmed"):
            backend._ensure_runtime()
            assert not recovery[1] and len(attempts) == 2
    finally:
        runtime.close = bridge.close = lambda: None


def test_failed_runtime_cleanup_keeps_its_bridge_owned(backend, recovery):  # noqa: F811
    idle_task(backend)
    attempts = []

    def fail_runtime():
        attempts.append("runtime")
        raise RpcError("runtime cleanup unconfirmed")

    runtime = SimpleNamespace(rpc=None, close=fail_runtime)
    bridge = SimpleNamespace(close=lambda: attempts.append("bridge"))
    backend.runtime, backend.bridge = runtime, bridge
    with pytest.raises(RpcError, match="cleanup unconfirmed"):
        backend.close()
    assert backend.runtime is runtime and backend.bridge is bridge
    assert attempts == ["runtime"]
    runtime.close = lambda: None
    backend.close()


def test_unconfirmed_bridge_cleanup_blocks_temporary_metadata_runtime(backend, recovery):  # noqa: F811
    idle_task(backend)

    def fail_bridge():
        raise RpcError("bridge cleanup unconfirmed")

    backend.runtime = None
    backend.bridge = SimpleNamespace(close=fail_bridge)
    with pytest.raises(RpcError, match="cleanup unconfirmed"):
        backend.close()
    with pytest.raises(RpcError, match="cleanup unconfirmed"):
        with backend._metadata_rpc():
            pytest.fail("metadata operation bypassed unconfirmed resource cleanup")
    assert not recovery[1]
    backend.bridge.close = lambda: None
    backend.close()


def test_healthy_idle_poll_keeps_runtime_without_rpc_request(backend, recovery):  # noqa: F811
    idle_task(backend)
    rpc, calls = ResumedRpc(), []
    rpc.poll_notifications = lambda: calls.append("notifications")
    rpc.poll_requests = lambda handler: calls.append("requests")
    runtime = SimpleNamespace(rpc=rpc, close=lambda: calls.append("close"))
    backend.runtime = runtime
    backend.poll_thread_names()
    assert calls == ["notifications", "requests"]
    assert backend.runtime is runtime and not rpc.calls and not recovery[1]
