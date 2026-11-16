"""Launch identity, alias priority, and private state creation boundaries."""

import os
import stat

import pytest

from sicomigration import admission, record
from sicostate import ensure, project_root, publish, root, startup_root


def test_published_launch_survives_chdir(tmp_path, monkeypatch):
    launch, changed = tmp_path / "launch", tmp_path / "changed"
    launch.mkdir()
    changed.mkdir()
    environment = {}
    assert publish(environment, launch) == launch / ".sico"
    monkeypatch.chdir(changed)
    assert root(environment=environment) == launch / ".sico"
    assert environment == {"SICO_TEMP_DIR": str(launch / ".sico")}
    assert not (launch / ".sico").exists()


def test_nas_spelling_is_preserved_and_equivalent_alias_accepted(tmp_path):
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = tmp_path / "NAS alias"
    alias.symlink_to(physical)
    environment = {"SICO_TEMP_DIR": str(alias / ".sico")}
    assert root(physical, environment=environment) == alias / ".sico"
    assert ensure(root(environment=environment)) == alias / ".sico"


@pytest.mark.parametrize("bad", ["", "relative/.sico", "/tmp/.cad", "/tmp/.sico/ai"])
def test_invalid_new_value_never_uses_legacy_fallback(tmp_path, bad):
    environment = {"SICO_TEMP_DIR": bad, "CAD_TEMP_DIR": str(tmp_path / ".sico")}
    with pytest.raises(ValueError):
        root(tmp_path, environment=environment)


def test_legacy_name_requires_migration_even_for_new_layout(tmp_path):
    for relative in (".sico", ".cad/ai"):
        with pytest.raises(ValueError, match="was removed"):
            publish({"CAD_TEMP_DIR": str(tmp_path / relative)}, tmp_path)


def test_conflicting_launch_is_rejected(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="conflicts"):
        root(tmp_path, environment={"SICO_TEMP_DIR": str(other / ".sico")})


def test_private_root_has_no_legacy_side_effects(tmp_path):
    selected = ensure(root(tmp_path, environment={}))
    assert selected.stat().st_mode & 0o777 == 0o700
    assert selected.stat().st_uid == os.getuid()
    assert not (tmp_path / ".cad").exists()


@pytest.mark.parametrize("kind", ["symlink", "public"])
def test_unsafe_or_unmigrated_root_is_never_repaired_implicitly(tmp_path, kind):
    selected = tmp_path / ".sico"
    if kind == "symlink":
        selected.symlink_to(tmp_path)
    else:
        selected.mkdir(mode=0o777)
        selected.chmod(0o777)
    with pytest.raises(ValueError):
        ensure(selected)
    if kind == "symlink":
        assert selected.is_symlink()
    else:
        assert selected.stat().st_mode & 0o777 == 0o777


def test_legacy_sibling_does_not_block_a_startup_root(tmp_path):
    legacy = tmp_path / ".cad"
    legacy.mkdir(mode=0o700)
    retained = legacy / "ai/agent/workspace.ini"
    retained.parent.mkdir(parents=True, mode=0o700)
    retained.write_bytes(b"legacy")
    selected = startup_root(tmp_path)
    assert selected.is_dir()
    assert selected.stat().st_mode & 0o777 == 0o700
    assert (selected / "legacy-ignored.json").is_file()
    assert startup_root(tmp_path) == selected
    assert project_root(tmp_path, create=True) == selected
    assert retained.read_bytes() == b"legacy"


def test_setgid_project_directory_keeps_legacy_isolation_admissible(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    project.chmod(0o2775)
    legacy = project / ".cad"
    legacy.mkdir()
    legacy.chmod(0o755)
    selected = startup_root(project)
    assert selected.stat().st_mode & stat.S_ISGID
    assert selected.stat().st_mode & 0o777 == 0o700
    assert admission(selected) is None


def test_setgid_inherited_record_directories_remain_readable(tmp_path):
    selected = tmp_path / ".sico"
    selected.mkdir()
    selected.chmod(0o2700)
    nested = selected / "nested"
    nested.mkdir()
    nested.chmod(0o2700)
    value = nested / "record.json"
    value.write_bytes(b"{}")
    value.chmod(0o600)
    assert record(selected, "nested/record.json") == {}


def test_legacy_root_without_a_startup_boundary_stays_read_only(tmp_path):
    (tmp_path / ".cad").mkdir(mode=0o700)
    with pytest.raises(ValueError, match="read-only"):
        ensure(tmp_path / ".sico")
    assert not (tmp_path / ".sico").exists()


def test_unmarked_parallel_root_still_requires_explicit_resolution(tmp_path):
    (tmp_path / ".cad").mkdir(mode=0o700)
    selected = tmp_path / ".sico"
    selected.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="completed migration activation"):
        ensure(selected)


def test_legacy_isolation_rejects_a_changed_source(tmp_path):
    legacy = tmp_path / ".cad"
    legacy.mkdir(mode=0o700)
    selected = startup_root(tmp_path)
    legacy.rename(tmp_path / "old-cad")
    legacy.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="source changed"):
        startup_root(tmp_path)
