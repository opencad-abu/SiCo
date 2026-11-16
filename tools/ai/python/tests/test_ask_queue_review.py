"""Ask admission and head reads preserve the task that owns a pending review."""

import threading
from contextlib import ExitStack

import pytest
from agent_service_business_fixtures import Provider, business_service
from agent_service_launch_fixtures import eventually
from sico.core.contracts import ModelTurn, TextDelta
from sico.service.backend import create_backend
from sico.transport.bridge_client import BridgeClient
from test_agent_service_control import attach
from test_agent_service_host import host_bridge, target
from test_ask_queue import ask


class FirstReview(Provider):
    def __init__(self):
        super().__init__()
        self.waiting, self.answer = threading.Event(), threading.Event()
        self.loop = None

    def stream(self, system, messages, tools, cancelled):
        self.seen.append(messages[-1]["content"])
        if len(self.seen) == 1:
            self.loop.state.task["waiting_audits"] = ["decision"]
            yield TextDelta("waiting")
            self.waiting.set()
            while not self.answer.wait(.01) and not cancelled():
                pass
        yield ModelTurn("waiting" if len(self.seen) == 1 else "done")


def test_review_blocks_second_ask_and_head_query_is_readonly(tmp_path, monkeypatch):
    provider = FirstReview()

    def backend(*args, **kwargs):
        loop = create_backend(*args, **kwargs)
        provider.loop = loop

        def answer(audit_id, answers, reply_id, actor, task_id):
            if task_id != loop.state.task["id"] or audit_id != "decision":
                return False
            loop.state.task["waiting_audits"] = []
            provider.answer.set()
            return True

        loop.audit.answer = answer
        return loop

    monkeypatch.setattr("sico.service.project_sessions.create_backend", backend)
    with host_bridge(tmp_path, target(tmp_path)), business_service(
            tmp_path, monkeypatch, provider=provider, bridge_factory=BridgeClient) as service, (
            ExitStack()) as stack:
        first = ask(tmp_path, target(tmp_path, "first"), 1)
        assert provider.waiting.wait(3)
        owner = service.owner.controllers[first.address.session.session_id]
        eventually(lambda: owner._task.get("waiting_audits") == ["decision"])
        try:
            second = ask(tmp_path, target(tmp_path, "second", view="layout"), 2)
            api, token = attach(stack, service, tmp_path, owner.session_id)
            before = owner.journal.events()
            assert not api.can_control(token)
            head = api.ask_head(owner.session_id, "second").result(5)
            assert head == second and head.queue_head == dict(input_id="first", ask_sequence=1)
            assert api.ask_head("missing", "second").result(5) is None
            assert api.ask_head(owner.session_id, "missing").result(5).outcome == "unknown"
            assert not api.can_control(token) and owner.journal.events() == before
            pending = [row["id"] for row in owner.inbox.pending]
            assert provider.seen == ["first"] and pending == ["second"]
            api.control.request(token, "acquire").result(5)
            task_id = owner._task["id"]
            with pytest.raises(ValueError, match="任务"):
                api.command(token, "answer_audit", owner.session_id, "old-task", "decision",
                            {}, "wrong-task").result(5)
            assert not api.command(token, "answer_audit", owner.session_id, task_id, "other-audit",
                                   {}, "wrong-audit").result(5)
            assert not provider.answer.is_set() and provider.seen == ["first"]
            assert api.command(token, "answer_audit", owner.session_id, task_id, "decision",
                               {"choice": "continue"}, "answer").result(5)
            assert owner.wait(5)
            assert provider.seen == ["first", "second"], (owner._task, owner.fault)
            assert api.ask_head(owner.session_id, "second").result(5).queue_head is None
        finally:
            provider.answer.set()
