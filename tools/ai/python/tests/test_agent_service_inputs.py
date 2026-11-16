"""Durable admission, cross-connection dedup and recovery without queue replay."""

import json
import threading
from dataclasses import replace

import pytest
from agent_service_business_fixtures import (
    CONTEXT,
    Provider,
    business_service,
    exchange,
    open_request,
    open_session,
    query_request,
    submit_request,
)
from agent_service_launch_fixtures import eventually
from sico.core.contracts import BoundContext
from sico.service.frontend_session import SessionToken
from sico.service.service_messages import ServiceRequest
from sico.storage.inbox_records import read_input
from sico.transport.framing import ProtocolError


def test_two_sessions_keep_targets_and_dedup_after_disconnect(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service:
        first = open_session(service)
        other_context = BoundContext("other", "generation", "other-target", {})
        second = exchange(service, open_request("other", context=other_context)).address
        a = exchange(service, submit_request(first))
        b = exchange(service, submit_request(second, context=other_context))
        assert a.outcome == b.outcome == "accepted"
        for address in (first, second):
            controller = service.owner.controllers[address.session.session_id]
            assert controller.wait(3)
            before = controller.journal.events()
            retry_context = CONTEXT if address == first else other_context
            controller.fault = "target has since disappeared"
            result = exchange(service, submit_request(address, context=retry_context))
            assert result.outcome == "accepted" and result.execution == "completed"
            after = controller.journal.events()
            assert after[:len(before)] == before
            assert all(event["kind"] == "session.control" for event in after[len(before):])
            assert len([event for event in before if event["kind"] == "task.started"]) == 1
            result = exchange(service, submit_request(
                address, text="changed", context=retry_context))
            assert result.code == "operation_conflict"
        assert first != second
        assert exchange(service, ServiceRequest()).pending.sessions == 2


def test_queue_full_and_closed_refusals_never_create_input(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    with business_service(tmp_path, monkeypatch, provider=Provider(entered, release)) as service:
        address = open_session(service)
        assert exchange(service, submit_request(address)).outcome == "accepted"
        assert entered.wait(3)
        for n in range(16):
            queued = exchange(service, submit_request(address, "queued_" + str(n)))
            assert queued.outcome == "accepted"
        result = exchange(service, submit_request(address, "overflow"))
        assert result.outcome == "rejected" and result.code == "queue_full"
        assert exchange(service, query_request(address, "overflow")).outcome == "unknown"
        controller = service.owner.controllers["live"]
        assert len(controller.inbox.pending) == 16
        controller.request_close()
        assert exchange(service, submit_request(address, "closed")).outcome == "rejected"
        assert exchange(service, submit_request(address)).outcome == "accepted"
        release.set()


def test_invalid_target_and_old_runtime_cannot_admit_input(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service:
        address = open_session(service)
        wrong = replace(address, session=SessionToken("live", "f" * 32))
        assert exchange(service, submit_request(wrong)).code == "stale_session"
        context = BoundContext("instance", "generation", "other", {})
        assert exchange(service, submit_request(address, context=context)).outcome == "rejected"
        controller = service.owner.controllers["live"]
        assert not controller.inbox.pending
        assert all(event["kind"] == "session.control" for event in controller.journal.events())
        with pytest.raises(ProtocolError):
            submit_request(replace(address, session=SessionToken("live", None)))


def test_restarted_service_queries_old_receipt_without_backend_or_replay(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service:
        address = open_session(service)
        controller = service.owner.controllers["live"]
        controller.paused = True
        assert exchange(service, submit_request(address)).outcome == "accepted"
    with business_service(tmp_path, monkeypatch) as restarted:
        result = exchange(restarted, query_request(address))
        assert result.outcome == "accepted" and result.execution == "not_started"
        assert not restarted.owner.controllers and not restarted.provider.seen
        assert restarted.descriptor.service_id != address.service_id
        assert exchange(restarted, submit_request(address)).outcome == "accepted"
        assert exchange(restarted, submit_request(address, "new")).code == "stale_session"
        assert exchange(restarted, open_request()).code == "history_readonly"


def test_directory_sync_failure_is_unknown_and_confirmation_does_not_requeue(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service:
        address = open_session(service)
        controller = service.owner.controllers["live"]
        with monkeypatch.context() as patch:
            patch.setattr("sico.service.inbox.sync_directory",
                          lambda path: (_ for _ in ()).throw(OSError("secret fault")))
            result = exchange(service, submit_request(address))
            assert result.outcome == "unknown" and result.code == "unconfirmed"
            assert "secret fault" not in str(result.record())
        result = exchange(service, query_request(address))
        assert result.outcome == "accepted" and result.recovery_required
        assert result.execution == "queued"
        assert exchange(service, submit_request(address)).recovery_required
        assert not controller.inbox.pending and not service.provider.seen


@pytest.mark.parametrize("damage", ["json", "identity", "status", "symlink", "hardlink"])
def test_corrupt_persistent_evidence_is_protocol_error_not_refusal(tmp_path, monkeypatch, damage):
    with business_service(tmp_path, monkeypatch) as service:
        address = open_session(service)
        controller = service.owner.controllers["live"]
        controller.paused = True
        exchange(service, submit_request(address))
        path = controller.inbox.directory / "input.json"
        row = json.loads(path.read_text())
        if damage == "json":
            path.write_text('{"id": "input", "id": "input"}')
        elif damage in {"identity", "status"}:
            row["service_address" if damage == "identity" else "status"] = "invalid"
            path.write_text(json.dumps(row))
        else:
            backup = path.with_suffix(".copy")
            path.rename(backup)
            if damage == "symlink":
                path.symlink_to(backup)
            else:
                import os
                os.link(backup, path)
        with pytest.raises(ProtocolError):
            exchange(service, query_request(address))
        # Teardown must not overwrite the deliberately damaged evidence.
        controller.inbox.pending.clear()


def test_bridge_protocol_damage_stays_protocol_error(tmp_path, monkeypatch):
    from sico.transport.bridge_client import BridgeClient

    def fail(_descriptor):
        BridgeClient._check_error(dict(kind="error", error="ProtocolError", message="damaged"))

    with business_service(tmp_path, monkeypatch, bridge_factory=fail) as service:
        with pytest.raises(ProtocolError):
            exchange(service, open_request())
        assert not service.owner.controllers
        assert exchange(service, ServiceRequest()).pending.sessions == 0


def test_completed_receipt_matches_only_original_runtime(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service:
        address = open_session(service)
        exchange(service, submit_request(address))
        eventually(lambda: exchange(service, query_request(address)).execution == "completed")
        row = read_input(service.owner.controllers["live"].inbox.directory, "input")
        assert row["service_address"] == address.record()
        wrong = replace(address, session=SessionToken("live", "e" * 32))
        with pytest.raises(ProtocolError):
            exchange(service, query_request(wrong))
