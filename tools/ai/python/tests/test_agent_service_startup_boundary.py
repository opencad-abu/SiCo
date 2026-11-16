"""Paused service persistence and readiness never occupy the Qt event thread."""

from concurrent.futures import CancelledError

import pytest
from agent_service_launch_fixtures import controlled_spawn, reap_controlled
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel

from sico.service.project_service import ProjectServiceLease
from sico.service.service_discovery import discover_project
from sico.service.service_startup import ServiceStartup, ServiceStartupError


@pytest.mark.parametrize("boundary", ["project.json", "discovery.json", "listener"])
@pytest.mark.parametrize("cancel", [False, True])
def test_paused_service_startup_returns_before_dependency_and_keeps_qt_alive(
    tmp_path, monkeypatch, boundary, cancel,
):
    entered, release, processes = controlled_spawn(monkeypatch, boundary=boundary)
    label = QLabel("opening")
    label.show()
    startup, attached = ServiceStartup(tmp_path), None
    try:
        assert entered.wait(5)
        heartbeat(label)
        assert not release.is_set() and not startup.ready.done()
        if cancel:
            startup.close()
            assert startup.wait(2)
            with pytest.raises(CancelledError):
                startup.ready.result()
            assert processes[0].is_alive()
            heartbeat(label)
            assert not release.is_set()
        release.set()
        if not cancel:
            first = startup.ready.result(5)
        attached = ServiceStartup(tmp_path)
        descriptor = attached.ready.result(5)
        assert descriptor.pid == processes[0].pid
        if not cancel:
            assert first == descriptor
    finally:
        release.set()
        startup.close()
        assert startup.wait(5)
        if attached:
            attached.close()
            assert attached.wait(5)
        reap_controlled(processes)
        label.close()


@pytest.mark.parametrize("boundary", ["project.json", "runtime", "publication", "discovery.json"])
def test_failed_service_reports_only_after_its_resources_are_released(
    tmp_path, monkeypatch, boundary,
):
    entered, release, processes = controlled_spawn(monkeypatch, boundary=boundary, failure=True)
    startup = ServiceStartup(tmp_path)
    try:
        assert entered.wait(5)
        release.set()
        with pytest.raises(ServiceStartupError, match="OSError"):
            startup.ready.result(5)
        assert startup.wait(2)
        processes[0].join(5)
        assert processes[0].exitcode == 1
        assert discover_project(tmp_path).state in {"absent", "inactive"}
        with ProjectServiceLease.acquire(tmp_path):
            pass
        assert not list(tmp_path.rglob("*.tmp"))
    finally:
        release.set()
        startup.close()
        assert startup.wait(5)
        reap_controlled(processes)


def test_timeout_detaches_but_does_not_kill_paused_service(tmp_path, monkeypatch):
    entered, release, processes = controlled_spawn(monkeypatch, boundary="project.json")
    startup = ServiceStartup(tmp_path, timeout=0.5)
    attached = None
    try:
        assert entered.wait(5)
        with pytest.raises(TimeoutError):
            startup.ready.result(5)
        assert startup.wait(2)
        assert processes[0].is_alive() and not release.is_set()
        release.set()
        attached = ServiceStartup(tmp_path)
        assert attached.ready.result(5).pid == processes[0].pid
    finally:
        release.set()
        startup.close()
        assert startup.wait(5)
        if attached:
            attached.close()
            assert attached.wait(5)
        reap_controlled(processes)
