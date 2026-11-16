"""Stale proofs, queued commands and uncertain grants never acquire implicit authority."""

import threading
from concurrent.futures import CancelledError
from contextlib import ExitStack
from dataclasses import asdict

import pytest
from agent_service_business_fixtures import business_service, open_session
from agent_service_launch_fixtures import eventually
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel
from test_agent_service_control import attach

from sico.service.frontend_hub import FrontendHub
from sico.service.service_channel import ServiceChannel
from sico.service.service_errors import RequestUnsent, ResultUnknown
from sico.service.session_control import SessionControl
from sico.transport.framing import ProtocolError


def control_events(controller):
    """控制权变更本身是证据；被拒的操作不允许留下任何一条。"""

    return [event for event in controller.journal.events()
            if event["kind"] == "session.control"]


def business_events(controller):
    return [event for event in controller.journal.events()
            if not event["kind"].startswith("session.control")]


@pytest.mark.parametrize("damage", ["missing", "lease", "client", "runtime", "generation", "bool"])
def test_invalid_control_never_mutates_or_cancels(tmp_path, monkeypatch, damage):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        address = open_session(service)
        api, token = attach(stack, service, tmp_path)
        grant = api.control.request(token, "acquire").result(5)
        proof = asdict(grant.claim)
        if damage == "missing":
            proof = None
        else:
            field, value = {"lease": ("lease_id", "f" * 32), "client": ("client_id", "f" * 32),
                "runtime": ("runtime_id", "f" * 32), "generation": ("generation", 2),
                "bool": ("generation", True)}[damage]
            proof[field] = value
        with pytest.raises(ValueError):
            api._commands.request("submit", dict(address=address.record(), args=["unauthorized"],
                                                 kwargs={}), control=proof).result(5)
        controller = service.owner.controllers["live"]
        assert not controller.inbox.pending and not business_events(controller)
        # 只有那次成功的 acquire 留下控制权证据；被拒的提交不留任何记录。
        assert [event["payload"]["action"] for event in control_events(controller)] == ["acquire"]
        assert not controller.closing and not service.provider.seen


def test_queued_old_command_is_rejected_after_takeover(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = FrontendHub.command
    def paused(hub, wire, operation, params):
        if operation == "submit" and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return original(hub, wire, operation, params)
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        a, token = attach(stack, service, tmp_path)
        b, _ = attach(stack, service, tmp_path)
        a.control.request(token, "acquire").result(5)
        b.control.request(token).result(5)
        monkeypatch.setattr(FrontendHub, "command", paused)
        receipt = a.command(token, "submit", "not yet admitted")
        try:
            assert entered.wait(3)
            b.control.request(token, "takeover").result(5)
            release.set()
            with pytest.raises(ValueError, match="只读"):
                receipt.result(5)
            assert not business_events(service.owner.controllers["live"])
        finally:
            release.set()


@pytest.mark.parametrize("completed", [False, True])
def test_discarded_control_grant_retires_its_connection(tmp_path, monkeypatch, completed):
    entered, release = threading.Event(), threading.Event()
    original = SessionControl.execute
    def paused(control, connection, params):
        result = original(control, connection, params)
        if params["action"] == "acquire":
            entered.set()
            assert release.wait(5)
        return result
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        a, token = attach(stack, service, tmp_path)
        b, _ = attach(stack, service, tmp_path)
        monkeypatch.setattr(SessionControl, "execute", paused)
        receipt = a.control.request(token, "acquire")
        widget = QLabel("waiting for authority")
        try:
            assert entered.wait(3)
            heartbeat(widget)
            assert not release.is_set() and not a.can_control(token)
            if completed:
                release.set()
                receipt.result(5)
            receipt.discard()
            release.set()
            with pytest.raises(CancelledError):
                receipt.result(5)
            eventually(lambda: b.control.request(token).result(5).state == "detached")
            assert not a.can_control(token)
            assert not service.owner.controllers["live"].closing
        finally:
            release.set()
            widget.close()


def test_lost_grant_reply_only_allows_observation_until_explicit_takeover(tmp_path, monkeypatch):
    original = ServiceChannel.exchange
    failed = []
    def lost(channel, request, **kwargs):
        result = original(channel, request, **kwargs)
        if (not failed and request.method == "frontend.command"
                and request.params["step"] == "read" and result.status.value["state"] == "ready"):
            failed.append(request.operation_id)
            raise RequestUnsent("Grant response lost")
        return result
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        monkeypatch.setattr(ServiceChannel, "exchange", lost)
        with pytest.raises(ResultUnknown):
            api.control.request(token, "acquire").result(5)
        assert not api.can_control(token)
        eventually(lambda: api.control.request(token).result(5).state == "recoverable")
        with pytest.raises(ProtocolError):
            api.control.request(token, "resume").result(5)
        api.control.request(token).result(5)
        api.control.request(token, "takeover").result(5)
        assert api.can_control(token)


def test_same_session_new_runtime_cannot_inherit_control(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, old = attach(stack, service, tmp_path)
        old_view = api.control.request(old, "acquire").result(5)
        context = service.owner.controllers["live"].current
        service.owner.close_session("live")
        service.owner.open("live", {}, context, environment={})
        token = api.open_session("live").result(5)
        assert token != old and not api.can_control(token)
        with pytest.raises(ValueError):
            api.control.request(old, "resume")
        with pytest.raises(ValueError):
            api._commands.request("initialize_context", dict(address=api.address(token).record(),
                args=[], kwargs={}), control=asdict(old_view.claim)).result(5)
        assert api.control.request(token).result(5).state == "available"
        assert api.control.request(token, "acquire").result(5).generation == 1


@pytest.mark.parametrize("damage", ["missing", "state", "generation", "client"])
def test_damaged_grant_is_protocol_error_and_never_publishes_control(tmp_path, monkeypatch, damage):
    original = FrontendHub.command
    def damaged(hub, wire, operation, params):
        result = original(hub, wire, operation, params)
        if operation == "control" and params["action"] == "acquire":
            if damage == "missing":
                result["claim"] = None
            elif damage == "state":
                result["state"] = "occupied"
            elif damage == "generation":
                result["generation"] += 1
                result["claim"]["generation"] += 1
            else:
                result["claim"]["client_id"] = "f" * 32
        return result
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        monkeypatch.setattr(FrontendHub, "command", damaged)
        with pytest.raises(ProtocolError):
            api.control.request(token, "acquire").result(5)
        assert not api.can_control(token)


def test_captured_host_intake_preserves_gui_control_ownership(tmp_path, monkeypatch):
    from test_agent_service_host import host_bridge, submit, target

    from sico.transport.bridge_client import BridgeClient

    context = target(tmp_path)
    with host_bridge(tmp_path, context) as (_bridge, client), business_service(
            tmp_path, monkeypatch, bridge_factory=BridgeClient) as service, ExitStack() as stack:
        accepted = submit(service, client, context)
        api, token = attach(stack, service, tmp_path, accepted.address.session.session_id)
        grant = api.control.request(token, "acquire").result(5)
        other = submit(service, client, target(tmp_path, "second-host-input"))
        assert other.outcome == "accepted" and other.address == accepted.address
        view = api.control.request(token).result(5)
        assert view.generation == grant.generation and api.can_control(token)
        assert service.owner.controllers[token.session_id].wait(5)
