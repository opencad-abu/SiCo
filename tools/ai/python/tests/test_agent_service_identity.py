"""Project identity survives aliases and restarts, but never silently follows a copy."""

import json
import os
import shutil
from pathlib import Path

import pytest

from sico.service.project_service import (
    ProjectBusy,
    ProjectServiceLease,
    initialize_project_copy,
)
from sico.service.service_discovery import discover_project
from sico.storage.project_files import RegistryError


def test_alias_and_restart_preserve_project_but_rotate_service(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(project, target_is_directory=True)
    with ProjectServiceLease.acquire(project) as first:
        project_id, service_id = first.project_id, first.service_id
        with pytest.raises(ProjectBusy):
            ProjectServiceLease.acquire(alias)
        assert discover_project(alias).project_id == project_id
    lock = project / ".sico/ai/agent/service/owner.lock"
    inode = lock.stat().st_ino
    with ProjectServiceLease.acquire(alias) as second:
        assert second.project_id == project_id
        assert second.service_id != service_id
    assert lock.stat().st_ino == inode
    assert discover_project(project).state == "inactive"


def test_different_projects_have_independent_ownership(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    with ProjectServiceLease.acquire(a) as first, ProjectServiceLease.acquire(b) as second:
        assert first.project_id != second.project_id


def test_copy_requires_explicit_initialization_and_does_not_affect_original(tmp_path):
    project, copied = tmp_path / "project", tmp_path / "copied"
    project.mkdir()
    with ProjectServiceLease.acquire(project) as first:
        shutil.copytree(project, copied)
        with pytest.raises(RegistryError, match="another directory"):
            ProjectServiceLease.acquire(copied)
        with pytest.raises(RegistryError, match="another directory"):
            discover_project(copied)
        with pytest.raises(RegistryError, match="supplied identity"):
            initialize_project_copy(copied, expected_project_id="0" * 32)
        new_id = initialize_project_copy(copied, expected_project_id=first.project_id)
        with ProjectServiceLease.acquire(copied) as second:
            assert second.project_id == new_id != first.project_id
        with pytest.raises(ProjectBusy):
            ProjectServiceLease.acquire(project)
    with pytest.raises(RegistryError, match="supplied identity"):
        initialize_project_copy(project, expected_project_id=first.project_id)


def test_discovery_does_not_create_or_modify_files(tmp_path):
    assert discover_project(tmp_path).state == "absent"
    assert list(tmp_path.iterdir()) == []
    with ProjectServiceLease.acquire(tmp_path):
        pass
    paths = tuple(tmp_path.rglob("*"))
    before = {str(p): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
              for p in paths}
    assert discover_project(tmp_path).state == "inactive"
    after = {str(p): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
             for p in tmp_path.rglob("*")}
    assert before == after


@pytest.mark.parametrize("payload", [b"broken", b"{}", b'{"protocol":1,"protocol":2}',
                                    b"x" * 16385])
def test_corrupt_identity_fails_closed_without_rewrite(tmp_path, payload):
    with ProjectServiceLease.acquire(tmp_path):
        pass
    path = tmp_path / ".sico/ai/agent/service/project.json"
    path.write_bytes(payload)
    for operation in (discover_project, ProjectServiceLease.acquire):
        with pytest.raises((RegistryError, ValueError)):
            operation(tmp_path)
        assert path.read_bytes() == payload


@pytest.mark.parametrize("field,value", [("directory_inode", True), ("directory_device", -1),
                                        ("project_id", "../x"), ("created_host", None)])
def test_identity_schema_is_strict(tmp_path, field, value):
    with ProjectServiceLease.acquire(tmp_path):
        pass
    path = tmp_path / ".sico/ai/agent/service/project.json"
    row = json.loads(path.read_bytes())
    row[field] = value
    path.write_text(json.dumps(row))
    with pytest.raises(RegistryError, match="identity"):
        ProjectServiceLease.acquire(tmp_path)


@pytest.mark.parametrize("name", ["project.json", "owner.lock"])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "public"])
def test_unsafe_registry_file_is_rejected(tmp_path, name, kind):
    with ProjectServiceLease.acquire(tmp_path):
        pass
    path = tmp_path / ".sico/ai/agent/service" / name
    if kind == "public":
        path.chmod(0o644)
    else:
        original = tmp_path / "outside"
        path.rename(original)
        if kind == "symlink":
            path.symlink_to(original)
        else:
            os.link(original, path)
    with pytest.raises((OSError, ValueError, RegistryError)):
        ProjectServiceLease.acquire(tmp_path)


@pytest.mark.parametrize("relative", ["", "ai", "ai/agent", "ai/agent/service"])
def test_registry_ancestor_symlink_is_rejected(tmp_path, relative):
    root = ".sico"
    path = tmp_path / root / relative
    parent = tmp_path
    for name in path.relative_to(tmp_path).parts[:-1]:
        parent = parent / name
        parent.mkdir(mode=0o700, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RegistryError, match="directory") as error:
        ProjectServiceLease.acquire(tmp_path)
    assert error.value.code == "unsafe_directory"
    assert not list(outside.iterdir())


def test_existing_legacy_registry_is_isolated_from_new_service_state(tmp_path):
    legacy = tmp_path / ".cad"
    legacy.mkdir(mode=0o700)
    marker = legacy / "retained.json"
    marker.write_bytes(b'{"legacy":true}')
    with ProjectServiceLease.acquire(tmp_path):
        assert (tmp_path / ".sico/legacy-ignored.json").is_file()
    assert marker.read_bytes() == b'{"legacy":true}'
    assert set(legacy.iterdir()) == {marker}


def test_missing_lock_is_not_recreated_for_existing_identity(tmp_path):
    with ProjectServiceLease.acquire(tmp_path):
        (tmp_path / ".sico/ai/agent/service/owner.lock").unlink()
        for operation in (ProjectServiceLease.acquire, discover_project):
            with pytest.raises(RegistryError, match="lost its ownership lock"):
                operation(tmp_path)
        assert not (tmp_path / ".sico/ai/agent/service/owner.lock").exists()


def test_private_modes_and_no_cwd_fallback(tmp_path, monkeypatch):
    with ProjectServiceLease.acquire(tmp_path):
        directory = tmp_path / ".sico/ai/agent/service"
        assert directory.stat().st_mode & 0o777 == 0o700
        for path in directory.iterdir():
            assert path.stat().st_mode & 0o777 == 0o600
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RegistryError, match="unavailable"):
        ProjectServiceLease.acquire(Path("missing"))
