"""Discovery reports candidates only while the fixed ownership lock is held."""

import json
from dataclasses import replace

import pytest
from agent_service_fixtures import private_socket
from sico.service import project_service, service_discovery
from sico.service.project_service import ProjectBusy, ProjectServiceLease
from sico.service.service_discovery import discover_project
from sico.storage.project_files import RegistryError, write_record


def test_publish_discover_retire_and_close_cannot_remove_new_record(tmp_path):
    with private_socket(tmp_path / "runtime") as endpoint:
        first = ProjectServiceLease.acquire(tmp_path)
        try:
            assert discover_project(tmp_path).state == "starting"
            descriptor = first.publish(endpoint)
            found = discover_project(tmp_path)
            assert found.state == "local_candidate"
            assert found.service == descriptor
            with pytest.raises(ProjectBusy) as error:
                ProjectServiceLease.acquire(tmp_path)
            assert error.value.discovery == found
        finally:
            first.close()
        assert discover_project(tmp_path).state == "inactive"
        with ProjectServiceLease.acquire(tmp_path) as second:
            assert discover_project(tmp_path).state == "starting"
            current = second.publish(endpoint)
            first.close()
            assert discover_project(tmp_path).service == current


def test_other_host_never_connects_checks_local_pid_or_takes_ownership(tmp_path, monkeypatch):
    remote = replace(
        service_discovery.local_host(),
        host_id="f" * 32,
        hostname="other-node",
        boot_id="00000000-0000-0000-0000-000000000000",
    )
    with private_socket(tmp_path / "runtime") as endpoint:
        with ProjectServiceLease.acquire(tmp_path) as owner:
            descriptor = replace(owner.publish(endpoint), host=remote)
            directory = tmp_path / ".sico/ai/agent/service"
            write_record(directory / "discovery.json", descriptor.record())
            monkeypatch.setattr(service_discovery, "process_identity",
                                lambda _pid: pytest.fail("Remote PID was checked locally"))
            found = discover_project(tmp_path)
            assert found.state == "other_host"
            assert found.service.host.hostname == "other-node"
            before = (directory / "discovery.json").read_bytes()
            with pytest.raises(ProjectBusy, match="another host: other-node"):
                ProjectServiceLease.acquire(tmp_path)
            assert (directory / "discovery.json").read_bytes() == before


def test_same_boot_host_identity_drift_still_verifies_the_local_owner(tmp_path):
    current = service_discovery.local_host()
    renamed = replace(current, host_id="f" * 32, hostname="renamed-node")
    with private_socket(tmp_path / "runtime") as endpoint:
        with ProjectServiceLease.acquire(tmp_path) as owner:
            descriptor = replace(owner.publish(endpoint), host=renamed)
            write_record(
                tmp_path / ".sico/ai/agent/service/discovery.json",
                descriptor.record(),
            )
            found = discover_project(tmp_path)
            assert found.state == "local_candidate"
            assert found.service == descriptor


def test_same_boot_host_identity_drift_without_local_process_is_unverified(
    tmp_path, monkeypatch,
):
    current = service_discovery.local_host()
    renamed = replace(current, host_id="f" * 32, hostname="renamed-node")
    with private_socket(tmp_path / "runtime") as endpoint:
        with ProjectServiceLease.acquire(tmp_path) as owner:
            descriptor = replace(owner.publish(endpoint), host=renamed)
            write_record(
                tmp_path / ".sico/ai/agent/service/discovery.json",
                descriptor.record(),
            )

            def missing(_pid):
                raise FileNotFoundError()

            monkeypatch.setattr(service_discovery, "process_identity", missing)
            assert discover_project(tmp_path).state == "unverified"


@pytest.mark.parametrize("changed", ["boot", "start", "missing", "zombie"])
def test_held_lock_with_unverifiable_process_never_becomes_candidate(
    tmp_path, monkeypatch, changed,
):
    with private_socket(tmp_path / "runtime") as endpoint:
        with ProjectServiceLease.acquire(tmp_path) as owner:
            descriptor = owner.publish(endpoint)
            if changed == "boot":
                descriptor = replace(descriptor, host=replace(
                    descriptor.host, boot_id="00000000-0000-0000-0000-000000000000"))
            elif changed == "start":
                descriptor = replace(descriptor,
                                     process_start=str(int(descriptor.process_start) + 1))
            elif changed == "missing":
                def missing(_pid):
                    raise FileNotFoundError()
                monkeypatch.setattr(service_discovery, "process_identity", missing)
            else:
                monkeypatch.setattr(service_discovery, "process_identity",
                                    lambda _pid: dict(start=descriptor.process_start, state="Z"))
            write_record(tmp_path / ".sico/ai/agent/service/discovery.json", descriptor.record())
            assert discover_project(tmp_path).state == "unverified"
            with pytest.raises(ProjectBusy):
                ProjectServiceLease.acquire(tmp_path)


@pytest.mark.parametrize("mutation", ["bad_json", "oversize", "project", "pid", "endpoint",
                                     "host", "extra", "duplicate"])
def test_corrupt_occupied_discovery_is_not_repaired_or_accepted(tmp_path, mutation):
    with private_socket(tmp_path / "runtime") as endpoint:
        with ProjectServiceLease.acquire(tmp_path) as owner:
            row = owner.publish(endpoint).record()
            if mutation in {"project", "pid", "endpoint", "host", "extra"}:
                key, value = {"project": ("project_id", "0" * 32), "pid": ("pid", True),
                              "endpoint": ("endpoint", "127.0.0.1:80"), "host": ("host", {}),
                              "extra": ("token", "secret")}[mutation]
                row[key] = value
            raw = {"bad_json": b"broken", "oversize": b"x" * 16385,
                   "duplicate": b'{"pid":1,"pid":2}'}.get(mutation, json.dumps(row).encode())
            path = tmp_path / ".sico/ai/agent/service/discovery.json"
            path.write_bytes(raw)
            for operation in (discover_project, ProjectServiceLease.acquire):
                with pytest.raises((RegistryError, ValueError)):
                    operation(tmp_path)
                assert path.read_bytes() == raw


def test_stale_corrupt_discovery_retires_only_after_lock_acquisition(tmp_path):
    with ProjectServiceLease.acquire(tmp_path):
        pass
    path = tmp_path / ".sico/ai/agent/service/discovery.json"
    path.write_bytes(b"corrupt")
    path.chmod(0o600)
    assert discover_project(tmp_path).state == "inactive"
    assert path.read_bytes() == b"corrupt"
    with ProjectServiceLease.acquire(tmp_path):
        assert not path.exists()


def test_publish_rejects_nonprivate_socket_and_closed_owner(tmp_path):
    with private_socket(tmp_path / "runtime") as endpoint:
        with ProjectServiceLease.acquire(tmp_path) as owner:
            endpoint.chmod(0o666)
            with pytest.raises(RegistryError, match="private"):
                owner.publish(endpoint)
            endpoint.chmod(0o600)
            owner.publish(endpoint)
        with pytest.raises(RegistryError, match="closed"):
            owner.publish(endpoint)


def test_publish_detects_replaced_lock_inode(tmp_path):
    with private_socket(tmp_path / "runtime") as endpoint:
        with ProjectServiceLease.acquire(tmp_path) as owner:
            path = tmp_path / ".sico/ai/agent/service/owner.lock"
            path.rename(path.with_suffix(".old"))
            path.touch(mode=0o600)
            with pytest.raises(RegistryError, match="replaced"):
                owner.publish(endpoint)
            with pytest.raises(RegistryError, match="replaced"):
                ProjectServiceLease.acquire(tmp_path)


def test_partial_construction_releases_lock(tmp_path, monkeypatch):
    original = project_service.process_identity
    monkeypatch.setattr(project_service, "process_identity",
                        lambda _pid: (_ for _ in ()).throw(OSError("proc unavailable")))
    with pytest.raises(OSError, match="proc unavailable"):
        ProjectServiceLease.acquire(tmp_path)
    monkeypatch.setattr(project_service, "process_identity", original)
    with ProjectServiceLease.acquire(tmp_path):
        pass


@pytest.mark.parametrize("held", [True, False])
def test_descriptor_without_identity_is_not_a_new_project(tmp_path, held):
    with private_socket(tmp_path / "runtime") as endpoint:
        with ProjectServiceLease.acquire(tmp_path) as owner:
            owner.publish(endpoint)
            (tmp_path / ".sico/ai/agent/service/project.json").unlink()
            if not held:
                owner.close()
            for operation in (discover_project, ProjectServiceLease.acquire):
                with pytest.raises(RegistryError, match="lost its project identity"):
                    operation(tmp_path)
