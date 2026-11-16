"""Session end is a durable intent followed by bounded, owned cleanup."""

from __future__ import annotations

import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from sico.service import session_end
from sico.service.control_contract import ControlRefused
from sico.service.frontend_session import SessionToken
from sico.service.service_session_dto import SessionAddress

PROJECT = "a" * 32
SERVICE = "b" * 32
RUNTIME = "c" * 32


def address():
    return SessionAddress(PROJECT, SERVICE, SessionToken("session", RUNTIME))


class Owner:
    def __init__(self, tmp_path):
        self.project = tmp_path
        self.controllers = {}
        self.calls = []

    def stop_observation(self, controller):
        self.calls.append("stop")

    def retire_session(self, controller, released):
        self.calls.append(("retire", released))
        released()


class Control:
    descriptor = SimpleNamespace(service_id=SERVICE)

    def __init__(self):
        self.calls = []
        self.refused = False
        self.lease = object()

    def guard(self, _address):
        self.calls.append(("guard", _address))
        return nullcontext(self.lease)

    def require(self, lease, connection, proof):
        self.calls.append(("require", lease, connection, proof))
        if self.refused:
            raise ControlRefused("refused")


class Journal:
    def __init__(self, directory="/journal"):
        self.directory = directory
        self.events = []
        self.state = {"state": True}

    def append_session(self, kind, payload, state):
        self.events.append((kind, payload, state))
        return {"sequence": 4}


class Changed:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def wait_for(self, predicate):
        assert predicate() is True


def controller(*, sealed=True):
    value = SimpleNamespace(
        runtime_id=RUNTIME,
        session_id="session",
        current={"context": True},
        _lock=threading.Lock(),
        journal=Journal(),
        inbox=SimpleNamespace(sealed=sealed),
        _thread=SimpleNamespace(join=lambda: None),
        loop=SimpleNamespace(close=lambda: None, _tools_changed=Changed(),
                             _inflight=set()),
        closing=False,
        _shutdown=SimpleNamespace(is_set=lambda: False),
    )
    value.request_close = lambda: None
    value.close = lambda: None
    return value


def service(tmp_path):
    owner = Owner(tmp_path)
    control = Control()
    retired = []
    value = session_end.SessionEnd(owner, control, retired=retired.append, timeout=.01)
    return value, owner, control, retired


def test_observe_uses_cached_live_and_durable_evidence(tmp_path, monkeypatch):
    value, owner, _control, _retired = service(tmp_path)
    target = address()
    cached = value._publish(target, "op", "ended")
    assert value.observe(target, "op") is cached

    owner.controllers["session"] = controller()
    assert value.observe(target, "other").state == "unknown"
    owner.controllers["session"].runtime_id = "d" * 32
    monkeypatch.setattr("sico.service.end_evidence.end_state",
                        lambda *_args: "ended")
    assert value.observe(target, "durable").state == "ended"


def test_request_returns_existing_or_raises_refused_unknown(tmp_path):
    value, _owner, _control, _retired = service(tmp_path)
    target = address()
    value._publish(target, "done", "ended")
    assert value.request(target, "done", "connection", "proof").state == "ended"

    value._begin = lambda *_args: (_ for _ in ()).throw(ControlRefused("refused"))
    with pytest.raises(ControlRefused):
        value.request(target, "new", "connection", "proof")

    value._publish(target, "new", "ended")
    assert value.request(target, "new", "connection", "proof").state == "ended"


def test_request_returns_a_concurrent_operation_result(tmp_path):
    value, _owner, _control, _retired = service(tmp_path)
    target = address()
    value._begin = lambda *_args: (_ for _ in ()).throw(ControlRefused("refused"))

    class Observe:
        def __init__(self):
            self.states = iter(("unknown", "ended"))

        def __call__(self, *_args):
            return SimpleNamespace(state=next(self.states))

    value.observe = Observe()
    assert value.request(target, "op", "connection", "proof").state == "ended"


def test_begin_checks_capacity_and_records_intent(tmp_path, monkeypatch):
    value, owner, control, _retired = service(tmp_path)
    target = address()
    owner.controllers["session"] = controller()
    started = []

    class Thread:
        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs

        def start(self):
            started.append(True)

    monkeypatch.setattr(session_end.threading, "Thread", Thread)
    view = value._begin(target, "op", "connection", "proof")
    assert view.state == "closing"
    assert started == [True]
    assert control.calls[0][0] == "guard"
    assert [row[0] for row in control.calls].count("require") == 2
    assert owner.controllers["session"].journal.events[0][0] == "session.end_requested"

    value._views = {("x", str(index)): view for index in range(256)}
    with pytest.raises(ValueError, match="记录已满"):
        value._begin(target, "new", "connection", "proof")


def test_begin_thread_failure_publishes_needs_reconcile(tmp_path, monkeypatch):
    value, owner, _control, _retired = service(tmp_path)
    owner.controllers["session"] = controller()

    class Thread:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("no threads")

    monkeypatch.setattr(session_end.threading, "Thread", Thread)
    with pytest.raises(RuntimeError, match="清理尚未完成"):
        value._begin(address(), "op", "connection", "proof")
    assert value._views[address(), "op"].state == "needs_reconcile"


def test_begin_evicts_only_terminal_history(tmp_path, monkeypatch):
    value, owner, _control, _retired = service(tmp_path)
    target = address()
    owner.controllers["session"] = controller()
    old = value._publish(target, "old", "ended")
    value._views.update({(target, str(index)): old for index in range(255)})
    started = []

    class Thread:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            started.append(True)

    monkeypatch.setattr(session_end.threading, "Thread", Thread)
    value._begin(target, "new", "connection", "proof")
    assert (target, "old") not in value._views and started == [True]


def test_finish_waits_for_original_cleanup_and_marks_timeout(tmp_path, monkeypatch):
    value, _owner, _control, _retired = service(tmp_path)
    target = address()
    value._publish(target, "op", "closing")
    waits = []

    class Task:
        def wait(self, timeout):
            waits.append(timeout)
            return False if len(waits) == 1 else True

    class Cleanup:
        def __init__(self, callback, name):
            self.callback, self.name = callback, name

        def start(self):
            return Task()

    monkeypatch.setattr(session_end, "CleanupTask", Cleanup)
    value._finish(controller(), target, "op")
    assert waits == [value.timeout, None]
    assert value._views[target, "op"].state == "needs_reconcile"

    class Failing(Task):
        def wait(self, timeout):
            raise RuntimeError("wait failed")

    class BadCleanup(Cleanup):
        def start(self):
            return Failing()

    monkeypatch.setattr(session_end, "CleanupTask", BadCleanup)
    value._finish(controller(), target, "other")
    assert value._views[target, "other"].state == "needs_reconcile"


def test_drain_moves_through_sealed_inflight_and_release(tmp_path, monkeypatch):
    value, owner, _control, retired = service(tmp_path)
    target = address()
    current = controller()
    released = []
    monkeypatch.setattr(session_end, "save_release",
                        lambda directory, address, operation, sequence:
                        released.append((directory, address, operation, sequence)))
    value._drain(current, target, "op")
    assert owner.calls[0] == "stop"
    assert current.journal.events[-1][0] == "session.end_observed"
    assert released == [("/journal", target, "op", 4)]
    assert retired == ["session"]
    assert value._views[target, "op"].state == "ended"


def test_drain_marks_unsealed_or_failed_cleanup_for_reconcile(tmp_path):
    value, owner, _control, _retired = service(tmp_path)
    target = address()
    value._drain(controller(sealed=False), target, "unsealed")
    assert value._views[target, "unsealed"].state == "needs_reconcile"
    owner.stop_observation = lambda _controller: (_ for _ in ()).throw(
        RuntimeError("stop failed"))
    value._drain(controller(), target, "failed")
    assert value._views[target, "failed"].state == "needs_reconcile"


def test_wait_reports_remaining_threads(tmp_path):
    value, _owner, _control, _retired = service(tmp_path)
    assert value.wait() is True
    joined = []

    class Thread:
        def __init__(self, alive):
            self.alive = alive

        def join(self, timeout):
            joined.append(timeout)

        def is_alive(self):
            return self.alive

    value._threads = {Thread(False)}
    assert value.wait(.1) is True
    value._threads = {Thread(True)}
    assert value.wait(None) is False
