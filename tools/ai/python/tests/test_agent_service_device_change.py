"""A persisted device number is an observation, not a project identity."""

import json
import shutil

import pytest

from sico.service.project_service import ProjectServiceLease, initialize_project_copy
from sico.service.service_discovery import discover_project
from sico.storage.project_files import RegistryError, write_record


@pytest.fixture
def renumbered_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with ProjectServiceLease.acquire(project) as owner:
        project_id = owner.project_id
    directory = project / ".sico/ai/agent/service"
    record = json.loads((directory / "project.json").read_bytes())
    record["directory_device"] += 1
    write_record(directory / "project.json", record)
    return project, directory, project_id


def test_device_change_preserves_identity_discovery_and_service(renumbered_project):
    project, directory, project_id = renumbered_project
    before = (directory / "project.json").read_bytes()
    lock_inode = (directory / "owner.lock").stat().st_ino
    alias = project.with_name("alias")
    alias.symlink_to(project, target_is_directory=True)
    assert discover_project(alias).project_id == project_id
    assert discover_project(project).state == "inactive"
    with ProjectServiceLease.acquire(alias) as owner:
        assert owner.project_id == project_id
        assert discover_project(project).state == "starting"
    assert (directory / "project.json").read_bytes() == before
    assert (directory / "owner.lock").stat().st_ino == lock_inode


def test_device_change_does_not_allow_copy_initialization(renumbered_project):
    project, directory, project_id = renumbered_project
    before = (directory / "project.json").read_bytes()
    with pytest.raises(RegistryError) as error:
        initialize_project_copy(project, expected_project_id=project_id)
    assert error.value.code == "not_a_copy"
    assert (directory / "project.json").read_bytes() == before


@pytest.mark.parametrize("damage", ["copied", "lock_replaced"])
def test_device_change_keeps_copy_and_lock_protection(renumbered_project, damage):
    project, directory, _project_id = renumbered_project
    if damage == "copied":
        copied = project.with_name("copied")
        shutil.copytree(project, copied)
        project = copied
        code = "project_copied"
    else:
        lock = directory / "owner.lock"
        lock.rename(directory / "original.lock")
        lock.write_bytes(b"")
        lock.chmod(0o600)
        code = "ownership_lost"
    for operation in (discover_project, ProjectServiceLease.acquire):
        with pytest.raises(RegistryError) as error:
            operation(project)
        assert error.value.code == code
