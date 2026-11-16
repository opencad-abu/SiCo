"""Crash cleanup needs the project lock, matching generation marker and dead process."""

import json
import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from agent_service_lifecycle_fixtures import service_process

from sico.service.background_watchdog import process_identity
from sico.service.project_service import ProjectBusy, ProjectServiceLease
from sico.service.service_discovery import local_host
from sico.service.service_retirement import retire_endpoint
from sico.storage.project_files import write_record


def test_sigkill_stale_endpoint_is_reclaimed_by_next_project_owner(tmp_path):
    with service_process(tmp_path) as (process, descriptor):
        root = Path(descriptor.endpoint).parent
        process.kill()
        process.wait(3)
        assert root.exists()
        with ProjectServiceLease.acquire(tmp_path):
            assert not root.exists()


def test_active_service_and_other_project_runtime_are_never_removed(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    with service_process(tmp_path) as (_process, descriptor):
        root = Path(descriptor.endpoint).parent
        with pytest.raises(ProjectBusy):
            ProjectServiceLease.acquire(tmp_path)
        with ProjectServiceLease.acquire(other) as owner:
            directory = other / ".sico/ai/agent/service"
            forged = replace(descriptor, project_id=owner.project_id, process_start="0")
            write_record(directory / "discovery.json", forged.record())
        with ProjectServiceLease.acquire(other):
            assert root.exists()


@pytest.mark.parametrize("mutation", ["marker", "symlink", "public", "extra", "host", "pid"])
def test_unverifiable_stale_runtime_is_left_untouched(tmp_path, mutation):
    with service_process(tmp_path) as (process, descriptor):
        root = Path(descriptor.endpoint).parent
        process.kill()
        process.wait(3)
        marker = root / "owner.json"
        directory = tmp_path / ".sico/ai/agent/service"
        if mutation == "marker":
            row = descriptor.record()
            row["service_id"] = "a" * 32
            write_record(marker, row)
        elif mutation == "symlink":
            marker.unlink()
            marker.symlink_to(directory / "discovery.json")
        elif mutation == "public":
            marker.chmod(0o666)
        elif mutation == "extra":
            (root / "unexpected").touch()
        else:
            if mutation == "host":
                changed = replace(descriptor, host=replace(descriptor.host, host_id="a" * 32))
            else:
                changed = replace(descriptor, pid=os.getpid(),
                                  process_start=process_identity(os.getpid())["start"])
            write_record(directory / "discovery.json", changed.record())
            write_record(marker, changed.record())
        try:
            with ProjectServiceLease.acquire(tmp_path):
                assert root.exists() and Path(descriptor.endpoint).exists()
        finally:
            shutil.rmtree(root)


def test_corrupt_stale_descriptor_cannot_direct_cleanup(tmp_path):
    with ProjectServiceLease.acquire(tmp_path):
        pass
    directory = tmp_path / ".sico/ai/agent/service"
    write_record(directory / "discovery.json", dict(endpoint="/tmp"))
    retire_endpoint(directory, "a" * 32, local_host())
    assert json.loads((directory / "discovery.json").read_text()) == dict(endpoint="/tmp")
