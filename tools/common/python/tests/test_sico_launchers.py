"""Exercise public dispatch in a relocated, isolated product installation."""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def installed(tmp_path):
    root = tmp_path / "version with spaces"
    for relative in ("bin/sico-tool-dispatch", "tools/common/sh/sico-installation.sh",
                     "etc/config/sico-install.json"):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    for name in ("drc", "lvs", "rce", "aiassistant", "mts-netlistor", "aivw"):
        (root / "bin" / name).symlink_to("sico-tool-dispatch")
    modules = {"drc": "drc", "lvs": "lvs", "rce": "rce", "aiassistant": "ai",
               "mts-netlistor": "mtsnl", "aivw": "aivw"}
    for name, module in modules.items():
        target = root / "tools" / module / "bin" / name
        target.parent.mkdir(parents=True)
        target.write_text('#!/bin/sh\nprintf "%s\\n" "$SICO_HOME" "$0" "$SICO_PYTHON" "$@"\n')
        target.chmod(0o755)
    return root


def run(entry, cwd, **environment):
    env = {"PATH": "/usr/bin:/bin", "CAD_PYTHON": "/legacy/python", **environment}
    return subprocess.run([str(entry), "argument with spaces", "$(literal)"],
                          cwd=cwd, env=env, text=True, capture_output=True, timeout=10)


@pytest.mark.parametrize("command", ["drc", "lvs", "rce", "aiassistant", "mts-netlistor", "aivw"])
def test_public_commands_resolve_one_installation_after_relocation(installed, tmp_path, command):
    relocated = tmp_path / "moved installation"
    installed.rename(relocated)
    alias = tmp_path / "site-current"
    alias.symlink_to(relocated)
    result = run(alias / "bin" / command, tmp_path, SICO_HOME=str(alias), SICO_PYTHON="/new/python")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == str(relocated)
    assert lines[1].startswith(str(relocated / "tools") + "/")
    assert lines[2:] == ["/new/python", "argument with spaces", "$(literal)"]


@pytest.mark.parametrize("configured", ["", "relative", "/unavailable"])
def test_bad_explicit_root_never_falls_back(installed, tmp_path, configured):
    result = run(installed / "bin/drc", tmp_path, SICO_HOME=configured, CAD_HOME=str(installed))
    assert result.returncode == 2
    assert not result.stdout


def test_other_marked_root_is_rejected(installed, tmp_path):
    other = tmp_path / "other"
    shutil.copytree(installed, other, symlinks=True)
    result = run(installed / "bin/drc", tmp_path, SICO_HOME=str(other))
    assert result.returncode == 2
    assert "conflicts" in result.stderr
    assert not result.stdout


def test_root_marker_and_tools_cannot_escape(installed, tmp_path):
    common = installed / "tools/common"
    outside = tmp_path / "outside"
    common.rename(outside)
    common.symlink_to(outside)
    result = run(installed / "bin/drc", tmp_path)
    assert result.returncode == 2
    assert "escapes" in result.stderr


def test_variable_values_are_never_interpreted_as_shell(installed, tmp_path):
    sentinel = tmp_path / "must-not-exist"
    value = "$(touch " + str(sentinel) + ")"
    result = run(installed / "bin/drc", tmp_path, SICO_PYTHON=value)
    assert result.returncode == 0
    assert result.stdout.splitlines()[2] == value
    assert not sentinel.exists()


def test_empty_python_is_rejected_before_tool_execution(installed, tmp_path):
    result = run(installed / "bin/drc", tmp_path, SICO_PYTHON="")
    assert result.returncode == 2
    assert "must not be empty" in result.stderr
    assert not result.stdout


def test_noncanonical_marker_is_rejected(installed, tmp_path):
    marker = installed / "etc/config/sico-install.json"
    marker.write_bytes(marker.read_bytes() + b"\n")
    result = run(installed / "bin/drc", tmp_path)
    assert result.returncode == 2
    assert "identity" in result.stderr
