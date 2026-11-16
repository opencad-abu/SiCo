"""Dependency pauses prove Qt submission, cancellation and detachment never wait for I/O."""

import threading
from concurrent.futures import CancelledError

import pytest
from agent_service_launch_fixtures import eventually
from agent_service_lifecycle_fixtures import paused_service, service_process
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel

import sico.service.service_client as client_module
from sico.service.service_client import ServiceClient
from sico.service.service_management import StopResultUnknown
from sico.service.service_messages import ServiceRequest
from sico.transport.framing import Connection
from sico_ui.receipts import DataReceipt


def worker_set():
    return {t for t in threading.enumerate() if t.name == "project-service-request"}


@pytest.mark.parametrize("action", ["receive", "cancel", "discard", "close"])
def test_socket_pause_leaves_qt_responsive_and_old_completion_unpublishable(tmp_path, action):
    before = worker_set()
    with paused_service(tmp_path) as (descriptor, entered, release):
        widget = QLabel("opening")
        client = ServiceClient(descriptor)
        completion = client.request()
        try:
            assert entered.wait(5)
            heartbeat(widget)
            assert not release.is_set() and not completion.done()
            if action == "receive":
                release.set()
                assert completion.result(3).status.state == "ready"
            else:
                getattr(client if action == "close" else completion, action)()
                heartbeat(widget)
                assert not release.is_set() and completion.done()
                with pytest.raises(CancelledError):
                    completion.result()
                eventually(lambda: worker_set() == before)
                release.set()
                with pytest.raises(CancelledError):
                    completion.result()
        finally:
            release.set()
            client.close()
            widget.close()
    eventually(lambda: worker_set() == before)


@pytest.mark.parametrize("cancel", [True, False])
def test_cancel_or_timeout_after_stop_does_not_claim_rollback(tmp_path, cancel):
    with paused_service(tmp_path, kind="stop", after=True) as (descriptor, entered, release):
        client = ServiceClient(descriptor)
        completion = client.request(ServiceRequest("service.stop"), timeout=0.5)
        widget = QLabel("stopping")
        try:
            assert entered.wait(5)
            heartbeat(widget)
            assert not release.is_set() and not completion.done()
            if cancel:
                completion.cancel()
            with pytest.raises(CancelledError if cancel else StopResultUnknown):
                completion.result(2)
        finally:
            release.set()
            client.close()
            widget.close()


def test_qt_receipt_replacement_drops_old_result_and_delivers_current_one(tmp_path):
    with paused_service(tmp_path) as (descriptor, entered, release):
        widget, delivered, failures = QLabel("query"), [], []
        receipt = DataReceipt(widget)
        client = ServiceClient(descriptor)
        first = client.request()
        receipt.watch(first, lambda value: delivered.append(("old", value)), failures.append)
        try:
            assert entered.wait(5)
            second = client.request()
            receipt.watch(second, lambda value: delivered.append(("current", value)),
                          failures.append)
            heartbeat(widget)
            assert not release.is_set() and first.cancelled() and not delivered
            release.set()
            second.result(3)
            heartbeat(widget)
            assert len(delivered) == 1 and delivered[0][0] == "current" and not failures
        finally:
            release.set()
            receipt.clear()
            client.close()
            widget.close()


def test_cancelled_workers_keep_their_capacity_until_blocked_send_has_released(
    tmp_path, monkeypatch,
):
    before = worker_set()
    entered, release = threading.Event(), threading.Event()
    original = Connection.send

    def paused_send(connection, row, **kwargs):
        if row.get("kind") == "request":
            entered.set()
            assert release.wait(5)
        return original(connection, row, **kwargs)

    with service_process(tmp_path) as (_process, descriptor):
        monkeypatch.setattr(client_module, "MAX_IN_FLIGHT", 1)
        monkeypatch.setattr(Connection, "send", paused_send)
        widget = QLabel("sending")
        client = ServiceClient(descriptor)
        completion = client.request()
        try:
            assert entered.wait(5)
            completion.cancel()
            heartbeat(widget)
            assert not release.is_set()
            with pytest.raises(RuntimeError, match="limit"):
                client.request()
            client.close()
            heartbeat(widget)
            assert not release.is_set()
        finally:
            release.set()
            client.close()
            widget.close()
    eventually(lambda: worker_set() == before)
