"""Session end is explicit, durable and distinct from detach and project stop."""

import threading
import time
from contextlib import ExitStack

import pytest
from agent_service_business_fixtures import Provider, business_service, open_session
from agent_service_launch_fixtures import eventually
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel
from test_agent_service_control import attach

from sico.service.service_management import exchange_service
from sico.service.service_messages import ServiceRequest
from sico.storage.journal import SessionJournal


def status(service, method="status"):
    return exchange_service(service.descriptor, ServiceRequest("service." + method),
                            deadline=time.monotonic() + 5).status


def test_end_one_idle_session_keeps_sibling_and_rejects_old_controller(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        open_session(service, "other")
        api, token = attach(stack, service, tmp_path)
        sibling, other = attach(stack, service, tmp_path, "other")
        api.control.request(token, "acquire").result(5)
        sibling.control.request(other, "acquire").result(5)
        assert api.end_session(token, "end-first").result(5).state == "closing"
        eventually(lambda: api.end_session(token, "end-first", observe=True).result(5).state
                   == "ended")
        assert not api.can_control(token)
        assert set(service.owner.controllers) == {"other"}
        assert status(service).pending.sessions == 1
        assert sibling.command(other, "submit", "sibling continues").result(5)
        assert service.owner.controllers["other"].wait(5)
        with pytest.raises(ValueError):
            api.command(token, "submit", "old runtime").result(5)
        with pytest.raises(ValueError):
            api.open_session("live").result(5)
        with SessionJournal(tmp_path, "live") as journal:
            assert [row["kind"] for row in journal.events()
                    if row["kind"] != "session.control"] == [
                "session.end_requested", "session.end_observed"]
            assert journal.events()[-1]["payload"]["state"] == "drained"
        assert api.end_session(token, "end-first").result(5).state == "ended"


def test_end_running_session_waits_for_task_and_seals_queue(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    provider = Provider(entered, release)
    with business_service(tmp_path, monkeypatch, provider=provider) as service, (
            ExitStack()) as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        first = api.command(token, "submit", "running").result(5)
        assert entered.wait(3)
        second = api.command(token, "submit", "queued").result(5)
        try:
            assert api.end_session(token, "end-running").result(5).state == "closing"
            eventually(lambda: service.owner.controllers["live"].closing)
            assert not release.is_set() and service.owner.controllers["live"].busy
            assert status(service).pending.sessions == 1
            assert api.query_input(token, second).result(5).status.execution == "not_started"
            release.set()
            eventually(lambda: api.end_session(token, "end-running", observe=True).result(5).state
                       == "ended")
            assert api.query_input(token, first).result(5).status.execution == "cancelled"
            assert provider.seen == ["running"]
            assert status(service, "stop").outcome == "stop_requested"
        finally:
            release.set()


def test_end_commit_paused_keeps_qt_and_listener_responsive(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = SessionJournal.append_session
    def paused(journal, kind, payload, context):
        if kind == "session.end_requested":
            entered.set()
            assert release.wait(8)
        return original(journal, kind, payload, context)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        observer, _ = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        monkeypatch.setattr(SessionJournal, "append_session", paused)
        receipt = api.end_session(token, "paused-end")
        widget = QLabel("ending")
        try:
            assert entered.wait(3)
            heartbeat(widget)
            assert not release.is_set() and not receipt.done()
            assert not service.owner.controllers["live"]._shutdown.is_set()
            assert status(service).pending.sessions == 1
            assert (observer.end_session(token, "paused-end", observe=True).result(5).state
                    == "unknown")
            release.set()
            assert receipt.result(5).state == "closing"
            eventually(lambda: observer.end_session(token, "paused-end",
                observe=True).result(5).state == "ended")
        finally:
            release.set()
            widget.close()


def test_observer_and_old_takeover_cannot_end_session(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        first, token = attach(stack, service, tmp_path)
        second, _ = attach(stack, service, tmp_path)
        with pytest.raises(ValueError, match="只读"):
            first.end_session(token, "observer").result(5)
        first.control.request(token, "acquire").result(5)
        second.control.request(token).result(5)
        second.control.request(token, "takeover").result(5)
        with pytest.raises(ValueError, match="只读"):
            first.end_session(token, "stale").result(5)
        assert not service.owner.controllers["live"]._shutdown.is_set()
        assert all(event["kind"] == "session.control"
                   for event in service.owner.controllers["live"].journal.events())


def test_uncertain_task_end_releases_runtime_but_preserves_unknown_outcome(tmp_path, monkeypatch):
    from sico.core.contracts import NeedsReconcile

    class Unknown(Provider):
        def stream(self, *args):
            raise NeedsReconcile("Tool was sent without a receipt")
            yield
    with business_service(tmp_path, monkeypatch, provider=Unknown()) as service, (
            ExitStack()) as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        api.command(token, "submit", "unknown tool").result(5)
        controller = service.owner.controllers["live"]
        assert controller.wait(5)
        assert controller._task["status"] == "needs_reconcile"
        api.end_session(token, "unknown-end").result(5)
        eventually(lambda: api.end_session(token, "unknown-end", observe=True).result(5).state
                   == "ended")
        assert not service.owner.controllers and controller.journal._fd == -1
        from sico.service.recovery_facts import read_facts
        facts = read_facts(tmp_path, "live")
        assert facts.task["status"] == "needs_reconcile"
        assert facts.resources_released and not facts.unresolved
        observed = status(service, "stop")
        assert observed.outcome == "stop_requested" and observed.pending.reconcile == 0
