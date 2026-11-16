"""Registry durability failures and blocked I/O remain confined to backend work."""

import os
import threading

import pytest
from agent_service_fixtures import private_socket
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel

from sico.service.project_service import ProjectBusy, ProjectServiceLease
from sico.service.service_discovery import discover_project
from sico.service.workbench_data import WorkbenchService
from sico.storage import project_files


@pytest.mark.parametrize("boundary", ["file_sync", "replace", "directory_sync"])
def test_identity_failure_releases_ownership_and_retry_recovers(tmp_path, monkeypatch, boundary):
    original_sync = project_files.os.fsync
    original_replace = project_files.os.replace
    original_directory = project_files.sync_directory

    def fail(*_args):
        raise OSError("injected persistence failure")

    if boundary == "file_sync":
        # Allow ancestor/lock durability; fail only the identity temporary file.
        def sync(fd):
            target = os.readlink("/proc/self/fd/" + str(fd))
            if target.endswith(".tmp"):
                fail()
            return original_sync(fd)
        monkeypatch.setattr(project_files.os, "fsync", sync)
    elif boundary == "replace":
        monkeypatch.setattr(project_files.os, "replace", fail)
    else:
        def sync_directory(path):
            if (path / "project.json").exists():
                fail()
            return original_directory(path)
        monkeypatch.setattr(project_files, "sync_directory", sync_directory)
    with pytest.raises(OSError, match="injected persistence failure"):
        ProjectServiceLease.acquire(tmp_path)
    assert not list(tmp_path.rglob("*.tmp"))
    monkeypatch.setattr(project_files.os, "fsync", original_sync)
    monkeypatch.setattr(project_files.os, "replace", original_replace)
    monkeypatch.setattr(project_files, "sync_directory", original_directory)
    with ProjectServiceLease.acquire(tmp_path):
        pass


def test_discovery_replace_failure_preserves_last_complete_record(tmp_path, monkeypatch):
    with private_socket(tmp_path / "runtime") as endpoint:
        with ProjectServiceLease.acquire(tmp_path) as owner:
            first = owner.publish(endpoint)
            def fail(*_args):
                raise OSError("replace failed")
            monkeypatch.setattr(project_files.os, "replace", fail)
            with pytest.raises(OSError, match="replace failed"):
                owner.publish(endpoint)
            assert discover_project(tmp_path).service == first
            assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("record", ["project.json", "discovery.json"])
def test_paused_fsync_does_not_deliver_success_or_block_qt(tmp_path, monkeypatch, record):
    worker = WorkbenchService()
    entered, release = threading.Event(), threading.Event()
    original = project_files.os.fsync
    qt_thread = threading.get_ident()
    label = QLabel("waiting")
    label.show()
    lease = None

    def paused(fd):
        target = os.readlink("/proc/self/fd/" + str(fd))
        if "/" + record + "." in target and target.endswith(".tmp"):
            assert threading.get_ident() != qt_thread
            entered.set()
            assert release.wait(5)
        return original(fd)

    try:
        with private_socket(tmp_path / "runtime") as endpoint:
            if record == "discovery.json":
                lease = ProjectServiceLease.acquire(tmp_path)
            monkeypatch.setattr(project_files.os, "fsync", paused)
            future = worker._request(lambda: (lease.publish(endpoint) if lease is not None
                                              else ProjectServiceLease.acquire(tmp_path)))
            assert entered.wait(2)
            heartbeat(label)
            assert not release.is_set() and not future.done()
            assert not (tmp_path / ".sico/ai/agent/service" / record).exists()
            with pytest.raises(ProjectBusy):
                ProjectServiceLease.acquire(tmp_path)
            release.set()
            result = future.result(3)
            if record == "project.json":
                lease = result
            else:
                assert discover_project(tmp_path).service == result
    finally:
        release.set()
        worker.close()
        worker.wait(5)
        if lease is not None:
            lease.close()
        label.close()
