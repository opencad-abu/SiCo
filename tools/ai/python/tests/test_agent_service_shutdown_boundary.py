"""Closing, input admission, lost replies and listener exit share explicit boundaries."""

import threading
from contextlib import ExitStack

import pytest
from agent_service_business_fixtures import business_service, open_session
from agent_service_launch_fixtures import eventually
from test_agent_service_control import attach
from test_agent_service_shutdown import status

from sico.service.frontend_hub import FrontendHub
from sico.service.inbox import SessionInbox
from sico.service.service_channel import ServiceChannel
from sico.service.service_errors import RequestUnsent, ResultUnknown
from sico.service.session_end_contract import SessionEndView
from sico.storage.journal import SessionJournal
from sico.transport.framing import ProtocolError


def test_end_after_inbox_commit_preserves_original_acceptance(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = SessionInbox._save
    def paused(inbox, record):
        if record["status"] == "queued" and not entered.is_set():
            entered.set()
            assert release.wait(8)
        return original(inbox, record)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        monkeypatch.setattr(SessionInbox, "_save", paused)
        submission = api.command(token, "submit", "admitted first")
        try:
            assert entered.wait(3)
            # Same control connection sequences these intents, even while the
            # first one waits on durable storage.
            ending = api.end_session(token, "after-admission")
            assert not submission.done() and not ending.done()
            assert status(service).pending.sessions == 1
            release.set()
            key = submission.result(5)
            ending.result(5)
            eventually(lambda: api.end_session(token, "after-admission",
                observe=True).result(5).state == "ended")
            assert api.query_input(token, key).result(5).status.outcome == "accepted"
        finally:
            release.set()


def test_queued_old_submit_cannot_pass_a_committed_end(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = FrontendHub.command
    def paused(hub, wire, operation, params):
        if operation == "submit":
            entered.set()
            assert release.wait(8)
        return original(hub, wire, operation, params)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        old, token = attach(stack, service, tmp_path)
        new, _ = attach(stack, service, tmp_path)
        old.control.request(token, "acquire").result(5)
        new.control.request(token).result(5)
        monkeypatch.setattr(FrontendHub, "command", paused)
        submission = old.command(token, "submit", "not yet admitted")
        try:
            assert entered.wait(3)
            new.control.request(token, "takeover").result(5)
            new.end_session(token, "end-wins").result(5)
            release.set()
            with pytest.raises(ValueError):
                submission.result(5)
            eventually(lambda: new.end_session(token, "end-wins", observe=True).result(5).state
                       == "ended")
            assert not service.provider.seen
        finally:
            release.set()


def test_lost_end_reply_is_queried_without_control_or_reexecution(tmp_path, monkeypatch):
    original = ServiceChannel.exchange
    lost = []
    def lose(channel, request, **kwargs):
        result = original(channel, request, **kwargs)
        if (request.operation_id == "lost-end" and request.params.get("step") == "read"
                and result.status.value["state"] == "ready" and not lost):
            lost.append(True)
            raise RequestUnsent("response lost after admission")
        return result
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        monkeypatch.setattr(ServiceChannel, "exchange", lose)
        with pytest.raises(ResultUnknown):
            api.end_session(token, "lost-end").result(5)
        eventually(lambda: api.end_session(token, "lost-end", observe=True).result(5).state
                   == "ended")
        assert not api.can_control(token)
        with SessionJournal(tmp_path, "live") as journal:
            assert sum(e["kind"] == "session.end_requested" for e in journal.events()) == 1


def test_failed_queue_seal_never_reports_ended(tmp_path, monkeypatch):
    original = SessionInbox.close
    def fail(inbox):
        raise OSError("injected durable queue failure")
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        monkeypatch.setattr(SessionInbox, "close", fail)
        api.end_session(token, "cannot-seal").result(5)
        eventually(lambda: api.end_session(token, "cannot-seal", observe=True).result(5).state
                   == "needs_reconcile")
        assert status(service).pending.sessions == 1
        monkeypatch.setattr(SessionInbox, "close", original)


@pytest.mark.parametrize("field", ["address", "operation_id", "state"])
def test_end_reply_identity_and_state_are_strict(tmp_path, monkeypatch, field):
    with business_service(tmp_path, monkeypatch) as service:
        address = open_session(service)
        row = SessionEndView(address, "intent", "closing").record()
        row[field] = {**row[field], "runtime_id": "f" * 32} if field == "address" else "wrong"
        with pytest.raises(ProtocolError):
            SessionEndView.from_record(row, address, "intent")
