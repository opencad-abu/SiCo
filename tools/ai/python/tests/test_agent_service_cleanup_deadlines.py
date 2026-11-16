"""Deadline observations retain ownership and accept only the original late cleanup."""

import threading
import time
from contextlib import ExitStack

import pytest
from agent_service_business_fixtures import business_service, open_session
from agent_service_launch_fixtures import eventually
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel
from sico.service.recovery_facts import read_facts
from sico.service.session_recovery import SessionRecovery
from sico.transport.framing import ProtocolError
from test_agent_service_control import attach
from test_agent_service_shutdown import status


@pytest.mark.parametrize("layer", ["task", "backend", "handler", "health", "resource"])
def test_end_deadline_keeps_writer_and_late_cleanup_is_single_attempt(tmp_path, monkeypatch,
                                                                     layer):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def pause():
        calls.append(layer)
        entered.set()
        assert release.wait(8)

    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        open_session(service, "other")
        api, token = attach(stack, service, tmp_path)
        other, other_token = attach(stack, service, tmp_path, "other")
        api.control.request(token, "acquire").result(5)
        other.control.request(other_token, "acquire").result(5)
        controller = service.owner.controllers["live"]
        ending = service.dispatch.frontend.ending
        ending.timeout = .08
        if layer == "task":
            controller._thread = threading.Thread(target=pause)
            controller._thread.start()
        elif layer == "backend":
            monkeypatch.setattr(controller.loop, "close", pause, raising=False)
        elif layer == "handler":
            controller.loop._tools_changed = threading.Condition()
            controller.loop._inflight = {"original"}
        elif layer == "health":
            monkeypatch.setattr(service.owner._bundles["live"].health, "wait", pause)
        else:
            service.owner.own_resource(controller, pause)
        label = QLabel("deadline")
        try:
            api.end_session(token, "bounded-end").result(5)
            if layer != "handler":
                assert entered.wait(2)
            eventually(lambda: api.end_session(token, "bounded-end", observe=True)
                       .result(5).state == "needs_reconcile")
            heartbeat(label)
            assert not ending.wait(.02)
            assert controller.journal._fd >= 0
            assert status(service).pending.sessions >= 1
            other.command(other_token, "submit", "sibling").result(5)
            assert service.owner.controllers["other"].wait(3)
            release.set()
            if layer == "handler":
                with controller.loop._tools_changed:
                    controller.journal.append_session("session.handler_receipt", {},
                                                      controller.current)
                    controller.loop._inflight.clear()
                    controller.loop._tools_changed.notify_all()
            eventually(lambda: api.end_session(token, "bounded-end", observe=True)
                       .result(5).state == "ended")
            assert controller.journal._fd == -1
            assert calls == ([] if layer == "handler" else [layer])
            assert read_facts(tmp_path, "live").resources_released
        finally:
            release.set()
            label.close()


def test_recovery_close_has_one_budget_and_retains_late_scan(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = SessionRecovery._scan
    def paused(recovery):
        entered.set()
        assert release.wait(5)
        original(recovery)
    monkeypatch.setattr(SessionRecovery, "_scan", paused)
    with business_service(tmp_path, monkeypatch) as service:
        try:
            assert entered.wait(2)
            start = time.monotonic()
            assert not service.dispatch.frontend.close(.05)
            assert time.monotonic() - start < .4
            assert service.dispatch.frontend.closed.is_set()
        finally:
            release.set()
        assert service.dispatch.frontend.close(3)


@pytest.mark.parametrize("damage", ["missing", "symlink", "size", "identity"])
def test_release_evidence_requires_matching_private_durable_receipt(tmp_path, monkeypatch, damage):
    from sico.storage.project_files import read_record, write_record

    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        api.command(token, "submit", "retain release evidence").result(5)
        assert service.owner.controllers["live"].wait(5)
        api.end_session(token, "release").result(5)
        eventually(lambda: api.end_session(token, "release", observe=True)
                   .result(5).state == "ended")
    path = (tmp_path / ".sico/ai/agent/sessions/live/end-releases" /
            (token.runtime_id + ".release.json"))
    assert not read_facts(tmp_path, "live").unresolved
    if damage == "missing":
        path.unlink()
        assert read_facts(tmp_path, "live").unresolved
        return
    if damage == "identity":
        row = read_record(path)
        row["operation_id"] = "different"
        write_record(path, row)
    elif damage == "size":
        path.write_bytes(b"x" * 17000)
    else:
        original = path.with_suffix(".saved")
        path.rename(original)
        path.symlink_to(original)
    with pytest.raises(ProtocolError):
        read_facts(tmp_path, "live")


def test_shared_local_peer_survives_until_last_session_retires(tmp_path, monkeypatch):
    closed = []
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        first = service.owner.controllers["live"]
        service.owner.own_resource(first, lambda: closed.append("peer"))
        second = service.owner.create_from("live", "other", first.current)
        assert service.owner._bundles["live"].resources is service.owner._bundles["other"].resources
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        api.end_session(token, "first").result(5)
        eventually(lambda: api.end_session(token, "first", observe=True).result(5).state == "ended")
        assert not closed
        assert not service.owner._bundles["other"].bridge.closed
        other, other_token = attach(stack, service, tmp_path, "other")
        other.control.request(other_token, "acquire").result(5)
        other.command(other_token, "submit", "still connected").result(5)
        assert second.wait(3)
        other.end_session(other_token, "last").result(5)
        eventually(lambda: other.end_session(other_token, "last", observe=True)
                   .result(5).state == "ended")
        assert closed == ["peer"]
