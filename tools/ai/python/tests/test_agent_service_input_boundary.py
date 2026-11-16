"""Dependency barriers prove durable ack timing and independent listener/Qt progress."""

import os
import threading
import time

import pytest
from agent_service_business_fixtures import (
    Bridge,
    business_service,
    exchange,
    open_request,
    open_session,
    query_request,
    submit_request,
)
from agent_service_control_fixtures import ControlledInputClient
from agent_service_launch_fixtures import eventually
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel

from sico.service.service_channel import service_channel
from sico.service.service_client import ServiceClient
from sico.service.service_inputs import ServiceInputs
from sico.service.service_messages import ServiceRequest


@pytest.mark.parametrize("boundary", ["file", "directory", "journal"])
def test_fsync_pause_never_acks_early_and_listener_qt_keep_running(tmp_path, monkeypatch, boundary):
    with business_service(tmp_path, monkeypatch) as service:
        address = open_session(service)
        entered, release = threading.Event(), threading.Event()
        original = os.fsync
        controller = service.owner.controllers["live"]
        controller.paused = boundary != "journal"

        def fsync(fd):
            path = os.readlink("/proc/self/fd/" + str(fd))
            match = {"file": path.endswith("/inbox/input.part"),
                     "directory": path.endswith("/inbox"),
                     "journal": path.endswith("/events.jsonl")}[boundary]
            if match and not release.is_set():
                entered.set()
                assert release.wait(10)
            return original(fd)

        monkeypatch.setattr(os, "fsync", fsync)
        widget, client = QLabel("receiving"), ControlledInputClient(service.descriptor)
        completion = client.request(submit_request(address))
        try:
            assert entered.wait(3)
            heartbeat(widget)
            assert not release.is_set()
            if boundary != "journal":
                assert not completion.done()
            snapshot = exchange(service, ServiceRequest())
            assert snapshot.pending.sessions == 1 and snapshot.state == "ready"
            with service_channel(service.descriptor, deadline=time.monotonic() + 2) as channel:
                assert channel.heartbeat(deadline=time.monotonic() + 2)
            release.set()
            assert completion.result(3).status.outcome == "accepted"
            assert exchange(service, query_request(address)).outcome == "accepted"
        finally:
            release.set()
            client.close()
            widget.close()


def test_open_bridge_pause_keeps_status_and_capacity_responsive(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def bridge(descriptor):
        entered.set()
        assert release.wait(10)
        return Bridge(descriptor)

    with business_service(tmp_path, monkeypatch, bridge_factory=bridge, capacity=1) as service:
        client, widget = ServiceClient(service.descriptor), QLabel("opening")
        completion = client.request(open_request())
        try:
            assert entered.wait(3)
            heartbeat(widget)
            assert exchange(service, ServiceRequest()).pending.sessions == 1
            refused = exchange(service, open_request("other"))
            assert refused.outcome == "rejected" and refused.code == "capacity"
            assert not completion.done() and not release.is_set()
            client.close()
            heartbeat(widget)
            assert completion.cancelled()
            release.set()
            eventually(lambda: "live" in service.owner.controllers)
            assert exchange(service, open_request()).outcome == "opened"
        finally:
            release.set()
            client.close()
            widget.close()


def test_disconnect_after_durable_commit_retries_same_id_once(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = ServiceInputs.submit

    def submit(self, *args):
        result = original(self, *args)
        entered.set()
        assert release.wait(10)
        return result

    with business_service(tmp_path, monkeypatch) as service:
        address = open_session(service)
        monkeypatch.setattr(ServiceInputs, "submit", submit)
        client = ControlledInputClient(service.descriptor)
        completion = client.request(submit_request(address))
        try:
            assert entered.wait(3)
            assert not completion.done()
            client.close()
            assert completion.cancelled()
            controller = service.owner.controllers["live"]
            assert controller.wait(3)
            release.set()
            for _ in range(3):
                assert exchange(service, submit_request(address)).outcome == "accepted"
            assert len(service.provider.seen) == 1
            assert exchange(service, query_request(address)).execution == "completed"
        finally:
            release.set()
            client.close()


def test_same_id_concurrent_observers_share_committed_acceptance(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from sico.service.service_management import exchange_service

    with business_service(tmp_path, monkeypatch) as service:
        address = open_session(service)
        assert exchange(service, submit_request(address)).outcome == "accepted"
        barrier = threading.Barrier(5)

        def call():
            barrier.wait(3)
            return exchange_service(service.descriptor, submit_request(address),
                                    deadline=time.monotonic() + 5).status

        with ThreadPoolExecutor(4) as pool:
            futures = [pool.submit(call) for _ in range(4)]
            barrier.wait(3)
            assert all(future.result(5).outcome == "accepted" for future in futures)
        assert service.owner.controllers["live"].wait(3)
        assert len(service.provider.seen) == 1
