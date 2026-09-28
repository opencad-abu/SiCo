"""State identity and EDA restoration across AI process handoffs."""

import os

import pytest

from cadenv import restore_eda_temp_environment
from sicotemp import ai_directory, initialize


def test_new_project_and_nested_handoff_restore_original_environment(tmp_path):
    environment = {"TMPDIR": "/vendor/tmp", "TEMP": "", "XDG_CACHE_HOME": "/vendor/cache"}
    original = dict(environment)
    temporary = initialize(environment, cwd=tmp_path)
    assert temporary == tmp_path / ".sico/ai"
    assert environment["SICO_TEMP_DIR"] == str(tmp_path / ".sico")
    assert "CAD_TEMP_DIR" not in environment
    assert not (tmp_path / ".cad").exists()
    for relative in (".sico", ".sico/ai", ".sico/ai/cache", ".sico/ai/runtime"):
        assert (tmp_path / relative).stat().st_mode & 0o777 == 0o700
    before = dict(environment)
    initialize(environment, cwd=tmp_path / "irrelevant-cwd")
    assert environment == before
    restored = restore_eda_temp_environment(environment)
    restored.pop("PYTHONDONTWRITEBYTECODE")
    assert restored == original


def test_existing_legacy_project_is_selected_without_parallel_new_root(tmp_path):
    (tmp_path / ".cad").mkdir(mode=0o700)
    environment = {}
    with pytest.raises(ValueError, match="read-only"):
        initialize(environment, cwd=tmp_path)
    assert environment == {}
    assert not list((tmp_path / ".cad").iterdir())
    assert not (tmp_path / ".sico").exists()


@pytest.mark.parametrize("value", ["", "relative/.sico", "/tmp/.sico/ai", "/tmp/.cad"])
def test_invalid_new_variable_never_falls_back(tmp_path, value):
    (tmp_path / ".cad").mkdir(mode=0o700)
    environment = {"SICO_TEMP_DIR": value, "CAD_TEMP_DIR": str(tmp_path / ".cad/ai")}
    with pytest.raises(ValueError):
        initialize(environment, cwd=tmp_path)
    assert list((tmp_path / ".cad").iterdir()) == []


@pytest.mark.parametrize("conflict", ["argument", "roots", "legacy-new-project"])
def test_conflicting_identity_is_rejected_before_writes(tmp_path, conflict):
    environment = {"SICO_TEMP_DIR": str(tmp_path / ".sico")}
    arguments = {}
    if conflict == "variables":
        environment["CAD_TEMP_DIR"] = str(tmp_path / ".cad/ai")
    elif conflict == "argument":
        arguments["temporary"] = tmp_path / "other/.sico"
    elif conflict == "roots":
        (tmp_path / ".cad").mkdir(mode=0o700)
        (tmp_path / ".sico").mkdir(mode=0o700)
    else:
        environment = {"CAD_TEMP_DIR": str(tmp_path / ".cad/ai")}
    before = set(tmp_path.rglob("*"))
    with pytest.raises(ValueError):
        initialize(environment, cwd=tmp_path, **arguments)
    assert set(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("kind", ["symlink-root", "symlink-child", "public-child", "orphan-marker"])
def test_unsafe_state_is_not_repaired(tmp_path, kind):
    root = tmp_path / ".sico"
    target = tmp_path / "keep"
    target.mkdir(mode=0o700)
    if kind == "symlink-root":
        root.symlink_to(target)
    else:
        root.mkdir(mode=0o700)
        if kind == "symlink-child":
            (root / "ai").symlink_to(target)
        elif kind == "public-child":
            (root / "ai").mkdir(mode=0o755)
        else:
            (root / "migration.json").write_text("{}")
    before = {str(path): path.lstat().st_mode for path in tmp_path.rglob("*")}
    with pytest.raises(ValueError):
        initialize({}, cwd=tmp_path)
    assert {str(path): path.lstat().st_mode for path in tmp_path.rglob("*")} == before


def test_nas_spelling_survives_chdir_and_equivalent_old_alias(tmp_path, monkeypatch):
    physical = tmp_path / "volume"
    physical.mkdir()
    alias = tmp_path / "NAS alias"
    alias.symlink_to(physical)
    environment = {"SICO_TEMP_DIR": str(alias / ".sico"),
                   "CAD_TEMP_DIR": str(physical / ".sico")}
    monkeypatch.chdir(tmp_path)
    assert initialize(environment) == alias / ".sico/ai"
    assert environment["SICO_TEMP_DIR"] == str(alias / ".sico")
    assert "CAD_TEMP_DIR" not in environment
    assert os.path.samefile(alias / ".sico", physical / ".sico")


def test_logical_cwd_is_kept_only_when_it_identifies_the_actual_cwd(tmp_path, monkeypatch):
    physical = tmp_path / "volume"
    physical.mkdir()
    alias = tmp_path / "NAS alias"
    alias.symlink_to(physical)
    monkeypatch.chdir(physical)
    assert ai_directory({"PWD": str(alias)}) == alias / ".sico/ai"
    assert ai_directory({"PWD": str(tmp_path)}) == physical / ".sico/ai"
