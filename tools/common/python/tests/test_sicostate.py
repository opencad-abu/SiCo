"""Launch identity, alias priority, and private state creation boundaries."""

import os

import pytest

from sicostate import ensure, publish, root


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


@pytest.mark.parametrize("kind", ["legacy", "symlink", "public"])
def test_unsafe_or_unmigrated_root_is_never_repaired_implicitly(tmp_path, kind):
    selected = tmp_path / ".sico"
    if kind == "legacy":
        (tmp_path / ".cad").mkdir(mode=0o700)
    elif kind == "symlink":
        selected.symlink_to(tmp_path)
    else:
        selected.mkdir(mode=0o777)
        selected.chmod(0o777)
    with pytest.raises(ValueError):
        ensure(selected)
    if kind == "legacy":
        assert not selected.exists()
    elif kind == "symlink":
        assert selected.is_symlink()
    else:
        assert selected.stat().st_mode & 0o777 == 0o777
