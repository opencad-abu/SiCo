"""Real async lifecycle exchanges publish typed receipts bound to the captured service."""

import socket
import threading
import time
from concurrent.futures import CancelledError
from dataclasses import FrozenInstanceError

import pytest
from agent_service_dto_fixtures import roundtrip
from agent_service_launch_fixtures import eventually
from agent_service_lifecycle_fixtures import service_process
from agent_service_protocol_fixtures import raw_channel, responder

from sico.service.service_client import ServiceClient
from sico.service.service_management import StopResultUnknown
from sico.service.service_messages import ServiceReceipt, ServiceRequest
from sico.service.service_protocol import request_message
from sico.service.service_ready import probe_ready
from sico.transport.framing import Connection, ProtocolError


def workers():
    return {t for t in threading.enumerate() if t.name == "project-service-request"}


def test_async_receipts_are_immutable_and_same_client_uses_new_authenticated_connections(tmp_path):
    before = workers()
    with service_process(tmp_path) as (process, descriptor):
        client = ServiceClient(descriptor, client_id="a" * 32)
        first = client.request().result(5)
        second = client.request().result(5)
        assert isinstance(first, ServiceReceipt)
        assert first.status.outcome == second.status.outcome == "observed"
        assert first.identity.client_id == second.identity.client_id == "a" * 32
        assert first.identity.project_id == descriptor.project_id
        assert first.identity.service_id == descriptor.service_id
        assert first.identity.connection_id != second.identity.connection_id
        wire = ServiceRequest(operation_id=first.operation_id).record(first.identity,
                                                                       first.request_id)
        assert ServiceReceipt.from_record(roundtrip(first.record()), wire, first.identity) == first
        with pytest.raises(FrozenInstanceError):
            first.status.pending.jobs = 1
        with pytest.raises(ProtocolError, match="captured connection"):
            ServiceReceipt.from_record(roundtrip(first.record()), wire,
                                       first.identity.__class__(first.identity.project_id,
                                                                first.identity.service_id,
                                                                first.identity.client_id,
                                                                "f" * 32,
                                                                first.identity.capabilities))
        client.close()
        client.close()
        assert process.poll() is None
        assert probe_ready(descriptor, deadline=time.monotonic() + 2) == descriptor
        with pytest.raises(RuntimeError, match="detached"):
            client.request()
    eventually(lambda: workers() == before)


def test_page_discard_hides_an_already_completed_result(tmp_path):
    with service_process(tmp_path) as (_process, descriptor):
        client = ServiceClient(descriptor)
        completion = client.request()
        assert completion.result(5).status.state == "ready"
        completion.discard()
        assert completion.done() and completion.cancelled()
        with pytest.raises(CancelledError):
            completion.result()
        client.close()


@pytest.mark.parametrize("change", [
    {"request_id": True}, {"connection_id": "f" * 32},
    {"service_id": "f" * 32}, {"method": "service.stop"},
    {"result": {"outcome": "rejected"}},
])
def test_async_malformed_receipt_is_protocol_error_without_automatic_retry(tmp_path, change):
    with responder(tmp_path, lambda row: dict(row, **change)) as (descriptor, listener, _):
        client = ServiceClient(descriptor)
        try:
            with pytest.raises(ProtocolError):
                client.request().result(3)
            listener.settimeout(0.05)
            with pytest.raises(socket.timeout):
                listener.accept()
        finally:
            client.close()


def test_stop_transport_loss_is_unknown_and_does_not_replay(tmp_path):
    with responder(tmp_path, lambda _row: None) as (descriptor, listener, lifecycle):
        client = ServiceClient(descriptor)
        try:
            with pytest.raises(StopResultUnknown):
                client.request(ServiceRequest("service.stop")).result(3)
            assert lifecycle.stopping
            listener.settimeout(0.05)
            with pytest.raises(socket.timeout):
                listener.accept()
        finally:
            client.close()


def test_receipt_operation_identity_mismatch_is_protocol_error(tmp_path):
    with service_process(tmp_path) as (_process, descriptor):
        client = ServiceClient(descriptor)
        first = client.request().result(5)
        captured = first.record()
        captured["operation_id"] = "b" * 32
        with pytest.raises(ProtocolError, match="Stale service response"):
            ServiceReceipt.from_record(captured,
                                       ServiceRequest(operation_id=first.operation_id).record(
                                           first.identity, first.request_id),
                                       first.identity)
        client.close()


def test_old_client_cannot_stop_replacement_service(tmp_path):
    with service_process(tmp_path) as (process, descriptor):
        client = ServiceClient(descriptor)
        process.terminate()
        process.wait(5)
    with service_process(tmp_path) as (replacement, new):
        try:
            with pytest.raises((OSError, ProtocolError)):
                client.request(ServiceRequest("service.stop")).result(3)
            assert new.service_id != descriptor.service_id and replacement.poll() is None
            assert probe_ready(new, deadline=time.monotonic() + 2) == new
        finally:
            client.close()


def test_encoding_decoding_and_status_publication_run_off_the_caller_thread(tmp_path, monkeypatch):
    owner, observed = threading.get_ident(), []
    original_send, original_receive = Connection.send, Connection.receive

    def send(connection, row, **kwargs):
        if row.get("kind") in ("hello", "request"):
            observed.append(threading.get_ident())
            assert threading.get_ident() != owner
        return original_send(connection, row, **kwargs)

    def receive(connection, **kwargs):
        result = original_receive(connection, **kwargs)
        if result.get("connection_id"):
            observed.append(threading.get_ident())
            assert threading.get_ident() != owner
        return result

    with service_process(tmp_path) as (_process, descriptor):
        monkeypatch.setattr(Connection, "send", send)
        monkeypatch.setattr(Connection, "receive", receive)
        client = ServiceClient(descriptor)
        try:
            assert client.request().result(5).status.state == "ready"
            assert len(observed) == 4 and len(set(observed)) == 1
        finally:
            client.close()


@pytest.mark.parametrize("params", [None, [], {"controller": {}}, {"force": True}])
def test_lifecycle_parameters_are_explicit_and_cannot_smuggle_other_operations(tmp_path, params):
    with service_process(tmp_path) as (_process, descriptor):
        with raw_channel(descriptor) as (connection, identity):
            row = request_message(identity, "service.stop", 1)
            row["params"] = params
            connection.send(row)
            with pytest.raises(EOFError):
                connection.receive(deadline=time.monotonic() + 2)
        assert probe_ready(descriptor, deadline=time.monotonic() + 2) == descriptor


def test_unimplemented_session_dtos_do_not_advertise_or_enable_business_methods():
    with pytest.raises(ProtocolError):
        ServiceRequest("session.submit")
