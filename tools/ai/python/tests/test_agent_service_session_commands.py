"""Only the current controller may answer, cancel or resume the captured task."""

import threading
from contextlib import ExitStack

import pytest
from agent_service_business_fixtures import Provider, business_service, open_session
from agent_service_launch_fixtures import eventually
from test_agent_service_control import attach
from test_agent_service_shutdown import status

from sico.core.contracts import ModelTurn, TextDelta
from sico.service.backend import create_backend
from sico.transport.framing import ProtocolError


def steering_scope(controller):
    return {
        "session_id": controller.session_id,
        "runtime_id": controller.runtime_id,
        "thread_id": "thread-captured-by-test",
        "turn_id": "turn-captured-by-test",
        "task_id": "task-captured-by-test",
        "context": controller.current.record(),
    }


def test_remote_frontend_routes_steer_to_current_controller(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        controller = service.owner.controllers["live"]
        scope = steering_scope(controller)
        calls = []

        def steer(text, *, scope, request_id):
            calls.append((text, scope, request_id))
            return {"status": "accepted", "scope": scope, "request_id": request_id}

        controller.backend_commands.steer = steer
        before = list(controller.journal.events())
        result = api.command(token, "steer", "Use the revised limit.",
                             scope=scope, request_id="steer-one").result(5)

        assert result["status"] == "accepted"
        assert result["request_id"] == "steer-one"
        assert calls == [("Use the revised limit.", scope, "steer-one")]
        assert not controller.inbox.pending
        assert controller.journal.events() == before


def test_remote_frontend_steer_requires_control_lease(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        controller = service.owner.controllers["live"]
        calls = []

        def steer(*args, **kwargs):
            calls.append((args, kwargs))
            return {"status": "accepted"}

        controller.backend_commands.steer = steer
        with pytest.raises(ValueError, match="只读"):
            api.command(token, "steer", "Unauthorized supplement",
                        scope=steering_scope(controller), request_id="steer-one").result(5)
        assert not calls


def test_remote_frontend_rejects_unrecognized_steering_scope_fields(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        controller = service.owner.controllers["live"]
        scope = steering_scope(controller)
        scope["unexpected"] = "must not cross the service boundary"
        before = list(controller.journal.events())
        with pytest.raises(ProtocolError, match="服务数据校验失败"):
            api.command(token, "steer", "Invalid supplement",
                        scope=scope, request_id="steer-one").result(5)
        assert controller.journal.events() == before


def test_remote_frontend_routes_thread_operation_to_current_controller(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        controller = service.owner.controllers["live"]
        scope = controller.thread_commands.thread_scope()
        calls = []

        def operate(action, *, scope, request_id):
            calls.append((action, scope, request_id))
            return request_id

        controller.thread_commands.thread_operation = operate
        result = api.command(
            token,
            "thread_operation",
            {"kind": "rollback_to", "before_turn_id": "turn-1"},
            scope=scope,
            request_id="rewind-one",
        ).result(5)

        assert result == "rewind-one"
        assert calls == [
            ({"kind": "rollback_to", "before_turn_id": "turn-1"}, scope, "rewind-one")
        ]


def test_remote_frontend_thread_operation_requires_control_lease(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        controller = service.owner.controllers["live"]
        calls = []

        def operate(*args, **kwargs):
            calls.append((args, kwargs))
            return "unexpected"

        controller.thread_commands.thread_operation = operate
        with pytest.raises(ValueError, match="只读"):
            api.command(
                token,
                "thread_operation",
                {"kind": "rollback_to", "before_turn_id": "turn-1"},
                scope=controller.thread_commands.thread_scope(),
                request_id="rewind-one",
            ).result(5)
        assert not calls


def test_cancel_is_task_bound_and_keeps_queue_until_explicit_resume(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    provider = Provider(entered, release)
    with business_service(tmp_path, monkeypatch, provider=provider) as service, (
            ExitStack()) as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        observer, _ = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        api.command(token, "submit", "first").result(5)
        assert entered.wait(3)
        api.command(token, "submit", "second").result(5)
        controller = service.owner.controllers["live"]
        task = controller.loop.state.task["id"]
        try:
            with pytest.raises(ValueError):
                observer.command(token, "cancel", expected_task=task).result(5)
            with pytest.raises(ValueError, match="任务已变化"):
                api.command(token, "cancel", expected_task="old-task").result(5)
            assert not controller._cancel.is_set()
            api.command(token, "cancel", expected_task=task).result(5)
            release.set()
            assert controller.wait(5)
            assert controller._task["status"] == "cancelled" and len(controller.inbox.pending) == 1
            assert not controller.closing and status(service).pending.sessions == 1
            api.command(token, "resume", expected_task=task).result(5)
            assert controller.wait(5)
            assert provider.seen == ["first", "second"]
        finally:
            release.set()


class WaitingDecision(Provider):
    def __init__(self):
        super().__init__()
        self.answer = threading.Event()
        self.waiting = threading.Event()
        self.loop = None

    def stream(self, system, messages, tools, cancelled):
        self.loop.state.task["waiting_audits"] = ["decision"]
        self.waiting.set()
        yield TextDelta("waiting")
        while not self.answer.wait(.01) and not cancelled():
            pass
        yield ModelTurn("answered")


def test_detach_leaves_approval_waiting_until_new_controller_answers(tmp_path, monkeypatch):
    provider = WaitingDecision()
    original = create_backend
    accepted = []
    def backend(*args, **kwargs):
        loop = original(*args, **kwargs)
        provider.loop = loop
        def answer(audit_id, answers, reply_id, actor, task_id):
            assert task_id == loop.state.task["id"] and audit_id == "decision"
            accepted.append((reply_id, answers))
            provider.answer.set()
            return True
        loop.audit.answer = answer
        return loop
    monkeypatch.setattr("sico.service.project_sessions.create_backend", backend)
    with business_service(tmp_path, monkeypatch, provider=provider) as service, (
            ExitStack()) as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        observer, _ = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        api.command(token, "submit", "need a decision").result(5)
        assert provider.waiting.wait(3)
        controller = service.owner.controllers["live"]
        eventually(lambda: status(service).pending.approvals == 1)
        task = controller.loop.state.task["id"]
        api.close_desktop()
        eventually(lambda: observer.control.request(token).result(5).state == "detached")
        assert controller.busy and not provider.answer.is_set()
        assert status(service).pending.approvals == 1
        answers = {"choice": "explicit"}
        try:
            with pytest.raises(ValueError):
                observer.command(token, "answer_audit", "live", task, "decision", answers,
                                 "answer-id").result(5)
            observer.control.request(token, "takeover").result(5)
            assert observer.command(token, "answer_audit", "live", task, "decision", answers,
                                    "answer-id").result(5)
            assert controller.wait(5)
            assert accepted == [("answer-id", answers)]
        finally:
            provider.answer.set()
