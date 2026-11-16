"""Stable operation identities retain bounded results without claiming durable acceptance."""

import threading
import time

import pytest
from agent_service_lifecycle_fixtures import service_process
from agent_service_listener_fixtures import listener_service
from agent_service_protocol_fixtures import responder
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel

from sico.service import service_wire
from sico.service.service_client import ServiceClient
from sico.service.service_errors import ServiceCapacityError, StopResultUnknown
from sico.service.service_lifecycle import PendingWork, ServiceLifecycle
from sico.service.service_management import exchange_service
from sico.service.service_messages import ServiceRequest
from sico.service.service_operations import OperationStatus, OperationStore
from sico.transport.framing import ProtocolError


def exchange(descriptor, request):
    return exchange_service(descriptor, request, deadline=time.monotonic() + 2)


def test_operation_result_is_stable_and_immutable_across_connections(tmp_path):
    with service_process(tmp_path) as (_process, descriptor):
        request = ServiceRequest()
        first = exchange(descriptor, request)
        repeated = exchange(descriptor, request)
        checked = exchange(descriptor, ServiceRequest("service.operation", request.operation_id))
        assert first.identity.connection_id != repeated.identity.connection_id
        assert first.status == repeated.status
        assert checked.status.method == request.method and checked.status.state == "completed"
        assert checked.status.operation_id == request.operation_id
        assert checked.status.result == first.status.record()
        with pytest.raises(TypeError):
            checked.status.result["pending"]["jobs"] = 2


def test_missing_operation_and_restarted_service_do_not_claim_success(tmp_path):
    with service_process(tmp_path) as (_process, descriptor):
        missing = exchange(descriptor, ServiceRequest("service.operation", "a" * 32))
        assert missing.status.state == "unknown" and missing.status.result is None
        request = ServiceRequest()
        exchange(descriptor, request)
    with service_process(tmp_path) as (_process, replacement):
        checked = exchange(replacement, ServiceRequest("service.operation", request.operation_id))
        assert replacement.service_id != descriptor.service_id
        assert checked.status.state == "unknown" and checked.status.result is None


def test_operation_reuse_is_checked_before_stop_and_original_record_survives(tmp_path):
    with service_process(tmp_path) as (process, descriptor):
        request = ServiceRequest()
        first = exchange(descriptor, request)
        with pytest.raises(StopResultUnknown):
            exchange(descriptor, ServiceRequest("service.stop", request.operation_id))
        checked = exchange(descriptor, ServiceRequest("service.operation", request.operation_id))
        assert checked.status.method == "service.status"
        assert checked.status.result == first.status.record() and process.poll() is None


def test_stop_requests_shutdown_even_with_pending_work(tmp_path):
    busy = threading.Event()
    busy.set()
    lifecycle = ServiceLifecycle(pending=lambda: PendingWork(jobs=int(busy.is_set())))
    with listener_service(tmp_path, lifecycle=lifecycle) as server:
        request = ServiceRequest("service.stop")
        first = exchange(server.descriptor, request)
        assert first.status.outcome == "stop_requested"
        assert lifecycle.stopping


def test_lost_stop_receipt_is_unknown_while_qt_keeps_running(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = service_wire.management_reply

    def paused(method, *args):
        result = original(method, *args)
        if method == "service.stop":
            entered.set()
            assert release.wait(3)
        return result

    monkeypatch.setattr(service_wire, "management_reply", paused)
    busy = threading.Event()
    busy.set()
    lifecycle = ServiceLifecycle(pending=lambda: PendingWork(jobs=int(busy.is_set())))
    with listener_service(tmp_path, lifecycle=lifecycle) as server:
        client = ServiceClient(server.descriptor)
        widget = QLabel("pending")
        request = ServiceRequest("service.stop")
        try:
            completion = client.request(request, timeout=0.2)
            assert entered.wait(1)
            heartbeat(widget)
            assert not release.is_set()
            with pytest.raises(StopResultUnknown) as raised:
                completion.result(1)
            assert raised.value.operation_id == request.operation_id
            release.set()
            assert lifecycle.stopping
        finally:
            busy.clear()
            release.set()
            client.close()
            widget.close()


def test_operation_cache_expires_evicts_and_preserves_first_result():
    clock = [0.0]
    store = OperationStore(capacity=1, ttl=60, clock=lambda: clock[0])
    original = store.record("a", "service.status", {}, {"value": [1]})
    assert store.record("a", "service.status", {}, {"value": [2]}) is original
    with pytest.raises(ProtocolError):
        store.record("a", "service.stop", {}, {})
    with pytest.raises(TypeError):
        original.result["value"].append(2)
    store.record("b", "service.status", {}, {})
    assert store.get("a") is None and store.get("b") is not None
    clock[0] = 60
    assert store.get("b") is None


@pytest.mark.parametrize("change", [
    {"operation_id": "wrong"}, {"state": "accepted"},
    {"method": "session.submit"}, {"result": {"outcome": "accepted"}},
    {"state": "completed"},
])
def test_corrupt_operation_lookup_never_becomes_completion(tmp_path, change):
    def corrupt(reply):
        reply["result"].update(change)
        return reply

    with responder(tmp_path, corrupt) as (descriptor, _listener, _lifecycle):
        with pytest.raises(ProtocolError):
            exchange(descriptor, ServiceRequest("service.operation", "original"))


def test_client_capacity_remains_owned_until_cancelled_worker_exits(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def paused(*_args, **_kwargs):
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr("sico.service.service_client.exchange_service", paused)
    monkeypatch.setattr("sico.service.service_client.MAX_IN_FLIGHT", 1)
    with service_process(tmp_path) as (_process, descriptor):
        client = ServiceClient(descriptor)
        completion = client.request()
        try:
            assert entered.wait(1)
            completion.cancel()
            with pytest.raises(ServiceCapacityError):
                client.request()
            client.close()
            assert completion.cancelled() and not release.is_set()
        finally:
            release.set()
            client.close()


@pytest.mark.parametrize("row", [
    dict(operation_id="a", state="unknown", method="service.stop", result=None),
    dict(operation_id="a", state="completed", method="service.stop", result={}),
])
def test_invalid_operation_status_is_protocol_error(row):
    with pytest.raises(ProtocolError):
        OperationStatus.from_record(row)
