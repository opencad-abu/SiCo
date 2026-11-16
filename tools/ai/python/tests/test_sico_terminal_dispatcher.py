"""Installed shell entry preserves the shared project and EDA environment contract."""

import pytest

from test_terminal_dispatcher import _install_dispatcher, _run_dispatcher


@pytest.mark.parametrize("legacy", [False, True])
def test_shell_creates_isolated_root_for_new_or_legacy_project(tmp_path, legacy):
    launcher = _install_dispatcher(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    legacy_root = project / ".cad"
    if legacy:
        legacy_root.mkdir(mode=0o700)
    result = _run_dispatcher(launcher, cwd=project, SICO_AI_PLATFORM="rhel8")
    assert result.returncode == 0, result.stderr
    root = project / ".sico"
    rows = result.stdout.splitlines()
    assert f"tmpdir={root}/ai" in rows
    assert f"sico-temp={root}" in rows
    assert "cad-temp=unset" in rows
    assert (root / "ai/runtime").stat().st_mode & 0o777 == 0o700
    if legacy:
        assert (root / "legacy-ignored.json").is_file()
        assert not list(legacy_root.iterdir())
    else:
        assert not legacy_root.exists()


@pytest.mark.parametrize("case", ["coexist", "new_legacy"])
def test_shell_rejects_invalid_identity_before_starting_terminal(tmp_path, case):
    launcher = _install_dispatcher(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    environment = {"SICO_AI_PLATFORM": "rhel8"}
    if case == "coexist":
        for name in (".cad", ".sico"):
            (project / name).mkdir(mode=0o700)
    else:
        environment["CAD_TEMP_DIR"] = str(project / ".cad/ai")
    before = sorted(project.rglob("*"))
    result = _run_dispatcher(launcher, cwd=project, **environment)
    assert result.returncode != 0
    assert "SiCo terminal:" in result.stderr
    assert not result.stdout
    assert sorted(project.rglob("*")) == before


def test_shell_inherited_nas_identity_survives_changed_cwd(tmp_path):
    launcher = _install_dispatcher(tmp_path)
    project, elsewhere = tmp_path / "project", tmp_path / "elsewhere"
    project.mkdir()
    elsewhere.mkdir()
    alias = tmp_path / "NAS alias"
    alias.symlink_to(project, target_is_directory=True)
    result = _run_dispatcher(launcher, cwd=elsewhere, SICO_AI_PLATFORM="rhel8",
                             SICO_TEMP_DIR=str(alias / ".sico"))
    assert result.returncode == 0, result.stderr
    assert f"sico-temp={alias}/.sico" in result.stdout.splitlines()
    assert (project / ".sico/ai").is_dir()
    assert not (elsewhere / ".sico").exists()


def test_shell_preserves_original_eda_temporary_markers(tmp_path):
    launcher = _install_dispatcher(tmp_path)
    terminal = launcher.with_name("sico-ai-terminal-pyqt")
    terminal.write_text('#!/bin/bash\n'
        'printf "original=%s\\n" "$SICO_ORIG_TMPDIR"\n'
        'printf "set=%s\\n" "$SICO_ORIG_TMPDIR_SET"\n'
        'printf "current=%s\\n" "$TMPDIR"\n')
    result = _run_dispatcher(launcher, SICO_AI_PLATFORM="rhel8", TMPDIR="/vendor scratch",
                             SICO_ORIG_TMPDIR_SET="1", SICO_ORIG_TMPDIR="/vendor original")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["original=/vendor original", "set=1",
                                         f"current={tmp_path}/.sico/ai"]
