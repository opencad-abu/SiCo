"""Slow and late cleanup preserves the writer, project admission and original result."""

import threading
from contextlib import ExitStack

import pytest
from agent_service_business_fixtures import Bridge, business_service, open_session
from agent_service_launch_fixtures import eventually
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel
from test_agent_service_control import attach
from test_agent_service_shutdown import status


def test_slow_bridge_release_keeps_writer_and_blocks_reopen_without_blocking_qt(tmp_path,
                                                                            monkeypatch):
    entered, release = threading.Event(), threading.Event()
    class SlowBridge(Bridge):
        def release_session(self, session_id):
            if session_id == "live":
                entered.set()
                assert release.wait(8)
    with business_service(tmp_path, monkeypatch, bridge_factory=SlowBridge) as service, (
            ExitStack()) as stack:
        open_session(service)
        open_session(service, "other")
        api, token = attach(stack, service, tmp_path)
        other, other_token = attach(stack, service, tmp_path, "other")
        api.control.request(token, "acquire").result(5)
        other.control.request(other_token, "acquire").result(5)
        controller = service.owner.controllers["live"]
        api.end_session(token, "slow-release").result(5)
        label = QLabel("waiting for cleanup")
        try:
            assert entered.wait(3)
            heartbeat(label)
            assert not release.is_set() and controller.journal._fd >= 0
            assert status(service).pending.sessions >= 1
            with pytest.raises(ValueError):
                api.open_session("live").result(5)
            other.command(other_token, "submit", "sibling while closing").result(5)
            assert service.owner.controllers["other"].wait(5)
            release.set()
            eventually(lambda: api.end_session(token, "slow-release", observe=True).result(5).state
                       == "ended")
        finally:
            release.set()
            label.close()


def test_late_handler_keeps_journal_open_until_original_receipt_arrives(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        controller = service.owner.controllers["live"]
        changed = threading.Condition()
        controller.loop._tools_changed = changed
        controller.loop._inflight = {"original-handler"}
        api.end_session(token, "late-handler").result(5)
        try:
            eventually(lambda: controller.closing)
            assert api.end_session(token, "late-handler", observe=True).result(5).state == "closing"
            assert controller.journal._fd >= 0 and status(service).pending.sessions >= 1
            controller.journal.append_session("session.handler_evidence", dict(original=True),
                                              controller.current)
        finally:
            with changed:
                controller.loop._inflight.clear()
                changed.notify_all()
        eventually(lambda: api.end_session(token, "late-handler", observe=True).result(5).state
                   == "ended")
        assert controller.journal._fd == -1


def test_failed_final_journal_commit_keeps_ended_result_unknown(tmp_path, monkeypatch):
    from sico.storage.journal import SessionJournal

    original = SessionJournal.append_session
    def fail(journal, kind, payload, context):
        if kind == "session.end_observed":
            raise OSError("terminal fsync unavailable")
        return original(journal, kind, payload, context)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        monkeypatch.setattr(SessionJournal, "append_session", fail)
        api.end_session(token, "failed-final").result(5)
        eventually(lambda: api.end_session(token, "failed-final", observe=True).result(5).state
                   == "needs_reconcile")
        assert "live" in service.owner.controllers
        assert status(service).pending.sessions >= 1


def test_background_work_is_visible_and_stop_is_still_requested(tmp_path, monkeypatch):
    from sico.service.background_service import BackgroundService

    pending = threading.Event()
    monkeypatch.setattr(BackgroundService, "busy", property(lambda _self: pending.is_set()))
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        pending.set()
        api.end_session(token, "background-running").result(5)
        eventually(lambda: api.end_session(token, "background-running", observe=True)
                   .result(5).state == "ended")
        observed = status(service)
        assert observed.pending.jobs >= 1
        pending.clear()
        assert status(service, "stop").outcome == "stop_requested"


def test_failed_bridge_cleanup_never_records_ended_success(tmp_path, monkeypatch):
    failed = threading.Event()
    class FailedBridge(Bridge):
        def close(self):
            if failed.is_set():
                raise OSError("bridge cleanup unavailable")
            super().close()
    with business_service(tmp_path, monkeypatch, bridge_factory=FailedBridge) as service, (
            ExitStack()) as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        controller = service.owner.controllers["live"]
        failed.set()
        try:
            api.end_session(token, "failed-release").result(5)
            eventually(lambda: api.end_session(token, "failed-release", observe=True)
                       .result(5).state == "needs_reconcile")
            assert controller.journal._fd >= 0
            event = controller.journal.events()[-1]
            assert event["kind"] == "session.end_observed"
            assert event["payload"]["state"] == "drained"
            assert status(service).pending.sessions >= 1
        finally:
            failed.clear()
