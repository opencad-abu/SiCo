"""Service status transport can stall without occupying Qt or stopping the service."""

import time
from concurrent.futures import CancelledError

import pytest
from agent_service_launch_fixtures import eventually
from agent_service_launch_fixtures import services as services
from agent_service_lifecycle_fixtures import paused_cleanup, paused_service
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel

from sico.service.project_service import ProjectBusy, ProjectServiceLease
from sico.service.service_admin import ServiceAdmin
from sico.service.service_discovery import discover_project
from sico.service.service_management import StopResultUnknown, exchange_service
from sico.service.service_messages import ServiceRequest
from sico.service.service_ready import probe_ready


@pytest.mark.parametrize("cancel", [False, True])
def test_status_socket_pause_keeps_qt_responsive_and_cancel_drops_late_result(tmp_path, cancel):
    with paused_service(tmp_path) as (descriptor, entered, release):
        label = QLabel("waiting")
        request = ServiceAdmin(tmp_path)
        try:
            assert entered.wait(5)
            heartbeat(label)
            assert not release.is_set() and not request.ready.done()
            if cancel:
                request.close()
                assert request.wait(2)
                with pytest.raises(CancelledError):
                    request.ready.result()
            release.set()
            if not cancel:
                assert request.ready.result(3)["state"] == "ready"
            assert probe_ready(descriptor, deadline=time.monotonic() + 2) == descriptor
        finally:
            release.set()
            request.close()
            assert request.wait(3)
            label.close()


@pytest.mark.parametrize("cancel", [True, False])
def test_stop_accepted_before_paused_reply_is_not_undone_by_cancel_or_timeout(tmp_path, cancel):
    with paused_service(tmp_path, kind="stop", after=True) as (descriptor, entered, release):
        label = QLabel("stopping")
        request = ServiceAdmin(tmp_path, stop=descriptor, timeout=0.5)
        try:
            assert entered.wait(5)
            heartbeat(label)
            assert not release.is_set() and not request.ready.done()
            if cancel:
                request.close()
            with pytest.raises(CancelledError if cancel else StopResultUnknown):
                request.ready.result(2)
            assert request.wait(2)
            release.set()
            eventually(lambda: discover_project(tmp_path).state == "inactive")
        finally:
            release.set()
            request.close()
            assert request.wait(3)
            label.close()


def test_stop_and_new_start_do_not_release_ownership_before_socket_cleanup(
    tmp_path, services,
):
    with paused_cleanup(tmp_path) as (descriptor, entered, release):
        label = QLabel("stopping")
        reply = exchange_service(
            descriptor, ServiceRequest('service.stop'), deadline=time.monotonic() + 2
        ).status.record()
        assert reply["outcome"] == "stop_requested"
        assert entered.wait(5)
        startup = services(tmp_path)
        try:
            heartbeat(label)
            assert not startup.ready.done() and not release.is_set()
            with pytest.raises(ProjectBusy):
                ProjectServiceLease.acquire(tmp_path)
            release.set()
            replacement = startup.ready.result(5)
            assert replacement.service_id != descriptor.service_id
        finally:
            release.set()
            label.close()
