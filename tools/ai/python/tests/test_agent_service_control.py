"""Per-session control is explicit, connection-bound and atomic with input admission."""

import threading
import time
from contextlib import ExitStack
from dataclasses import asdict, replace

import pytest
from agent_service_business_fixtures import business_service, open_session, submit_request
from agent_service_gui_fixtures import drain
from agent_service_launch_fixtures import eventually
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel

from sico.service.remote_frontend import RemoteFrontend
from sico.service.service_channel import service_channel
from sico.service.service_management import exchange_service
from sico.service.service_messages import ServiceRequest
from sico.transport.framing import ProtocolError


def attach(stack, service, project, session="live"):
    api = RemoteFrontend(service.descriptor, project, session)
    stack.callback(api.close_desktop)
    frontend = api.open_initial("attach", dict(session_id=session)).result(5)
    return api, frontend.session


def test_observers_takeover_and_sessions_are_independent(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        open_session(service, "other")
        a, token = attach(stack, service, tmp_path)
        b, _ = attach(stack, service, tmp_path)
        c, other = attach(stack, service, tmp_path, "other")
        with pytest.raises(ValueError, match="只读"):
            b.command(token, "submit", "observer cannot send").result(5)
        granted = a.control.request(token, "acquire").result(5)
        c.control.request(other, "acquire").result(5)
        assert a.can_control(token) and c.can_control(other)
        assert b.control.request(token).result(5).state == "occupied"
        with pytest.raises(ValueError):
            b.control.request(token, "acquire").result(5)
        first = a.command(token, "submit", "accepted before takeover").result(5)
        assert service.owner.controllers["live"].wait(5)
        view = b.control.request(token, "takeover").result(5)
        assert view.generation == granted.generation + 1
        assert b.can_control(token) and c.can_control(other)
        with pytest.raises(ValueError, match="只读"):
            a.command(token, "submit", "stale writer").result(5)
        a.control.request(token).result(5)
        assert not a.can_control(token)
        assert a.query_input(token, first).result(5).status.outcome == "accepted"
        replay = a.prepare_replay(token, 1, 200).result(5)
        assert drain(a, replay.stream)[-1].end > 0
        b.control.request(token, "release").result(5)
        assert not b.can_control(token)
        a.control.request(token).result(5)
        a.control.request(token, "acquire").result(5)
        assert a.can_control(token)


def test_reconnect_never_reuses_old_proof_and_resume_is_explicit(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        address = open_session(service)
        api, token = attach(stack, service, tmp_path)
        initial = api.control.request(token, "acquire").result(5)
        proof = asdict(initial.claim)
        epoch = api._commands.epoch
        with pytest.raises(ProtocolError):
            api._commands.request("invalid", {}).result(5)
        assert api._commands.epoch > epoch and not api.can_control(token)
        prepared = api.prepare_replay(token, 2, 200).result(5)
        drain(api, prepared.stream)
        assert not api.can_control(token)
        with pytest.raises(ValueError):
            api._commands.request("submit", dict(address=address.record(), args=["no resume"],
                                                 kwargs={}), control=proof).result(5)
        def detached():
            return api.control.request(token).result(5).state == "recoverable"
        eventually(detached)
        fresh = api.control.request(token, "resume").result(5)
        assert fresh.claim.lease_id != initial.claim.lease_id and api.can_control(token)
        assert fresh.generation > initial.generation
        with pytest.raises(ValueError):
            api._commands.request("initialize_context", dict(address=address.record(), args=[],
                                                            kwargs={}), control=proof).result(5)
        assert api.command(token, "submit", "resumed once").result(5)


def test_simultaneous_takeovers_use_observed_generation(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        a, token = attach(stack, service, tmp_path)
        b, _ = attach(stack, service, tmp_path)
        c, _ = attach(stack, service, tmp_path)
        a.control.request(token, "acquire").result(5)
        b.control.request(token).result(5)
        c.control.request(token).result(5)
        left, right = b.control.request(token, "takeover"), c.control.request(token, "takeover")
        accepted = 0
        for receipt in (left, right):
            try:
                receipt.result(5)
                accepted += 1
            except ValueError:
                pass
        assert accepted == 1


def test_legacy_submit_endpoint_cannot_bypass_lease_or_new_connection(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service:
        address = open_session(service)
        deadline = time.monotonic() + 10
        with service_channel(service.descriptor, deadline=deadline) as channel:
            request = submit_request(address)
            assert channel.exchange(request, deadline=deadline).status.code == "control_required"
            lease = channel.exchange(ServiceRequest("session.control", params=dict(
                address=address.record(), action="acquire", generation=0, previous=None)),
                deadline=deadline).status
            proof = asdict(lease.claim)
            controlled = replace(request, control=proof)
            assert channel.exchange(controlled, deadline=deadline).status.outcome == "accepted"
            with service_channel(service.descriptor, client_id=channel.identity.client_id,
                                 deadline=deadline) as old_identity:
                fresh = replace(controlled, operation_id="new-connection")
                result = old_identity.exchange(fresh, deadline=deadline).status
                assert result.code == "control_required"
        # Stable accepted facts are still readable without control and never reexecute.
        assert exchange_service(service.descriptor, request,
                                deadline=deadline).status.outcome == "accepted"


def test_fsync_admission_orders_takeover_without_blocking_listener_or_qt(tmp_path, monkeypatch):
    from sico.service.inbox import SessionInbox

    entered, release = threading.Event(), threading.Event()
    original = SessionInbox._save
    def slow(inbox, row):
        if row["id"] == "paused" and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return original(inbox, row)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        address = open_session(service)
        a, token = attach(stack, service, tmp_path)
        b, _ = attach(stack, service, tmp_path)
        a.control.request(token, "acquire").result(5)
        b.control.request(token).result(5)
        monkeypatch.setattr(SessionInbox, "_save", slow)
        receipt = a._commands.request("submit", dict(address=address.record(), args=["once"],
            kwargs={}), operation_id="paused", control=a.control.proof(token))
        widget = QLabel("control ordering")
        try:
            assert entered.wait(3)
            takeover = b.control.request(token, "takeover")
            heartbeat(widget)
            assert not release.is_set() and not receipt.done() and not takeover.done()
            assert exchange_service(service.descriptor, ServiceRequest(),
                                    deadline=time.monotonic()+3).status.pending.sessions == 1
            release.set()
            assert receipt.result(5) == "paused"
            assert takeover.result(5).state == "owned"
            with pytest.raises(ValueError):
                a.command(token, "submit", "too late").result(5)
            assert service.owner.controllers["live"].wait(5)
            assert service.provider.seen == ["once"]
        finally:
            release.set()
            widget.close()


def test_control_changes_are_recorded_as_session_evidence(tmp_path, monkeypatch):
    """控制权变更（含接管一个已断开窗口）要写进会话记录，事后能核对是谁改的。"""

    def actions(service):
        return [event["payload"] for event in service.owner.controllers["live"].journal.events()
                if event["kind"] == "session.control"]

    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        a, token = attach(stack, service, tmp_path)
        a.control.request(token, "acquire").result(5)
        b, _ = attach(stack, service, tmp_path)
        assert b.control.request(token).result(5).state == "occupied"

        a.close_desktop()  # 模拟窗口崩溃：持有控制的连接直接断开
        eventually(lambda: b.control.request(token).result(5).state == "detached")
        assert b.control.request(token, "takeover").result(5).state == "owned"
        b.control.request(token, "release").result(5)

        recorded = actions(service)
        assert [row["action"] for row in recorded] == ["acquire", "takeover", "release"]
        assert recorded[0]["previous_client_id"] == ""
        assert recorded[1]["previous_client_id"] == a._commands.client_id
        assert recorded[1]["previous_disconnected"] is True  # 自动接管的判据
        assert recorded[1]["generation"] == 2
