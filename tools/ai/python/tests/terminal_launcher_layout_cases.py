"""Terminal launcher layout regressions."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from pinyin_wrapper_support import product_layout

from terminal_fixtures import (
    PYQT_ROOT,
    REQUIRED_TERMINAL_PACKAGE_FILES,
    _run_test_terminal_launcher,
    _stage_test_terminal_launcher,
    _stage_test_terminal_package,
)


def test_pyqt_launcher_supports_source_and_installed_layouts(tmp_path: Path) -> None:
    from pinyin_wrapper_support import dispatcher_environment, product_layout

    launcher_source = PYQT_ROOT / "sico-ai-terminal-pyqt"
    installed_root = product_layout(tmp_path) / "tools/ai"
    launcher = installed_root / "bin" / "sico-ai-terminal-pyqt"
    package = installed_root / "python" / "cad_ai_terminal"
    launcher.parent.mkdir(parents=True)
    package.mkdir(parents=True)
    launcher.write_bytes(launcher_source.read_bytes())
    launcher.chmod(0o755)
    for name in REQUIRED_TERMINAL_PACKAGE_FILES:
        (package / name).write_bytes((PYQT_ROOT / "cad_ai_terminal" / name).read_bytes())

    probe = tmp_path / "probe"
    probe.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    probe.chmod(0o755)
    environment = dispatcher_environment()
    environment.update(
        {
            "SICO_PYTHON": sys.executable,
            "PYTHONDONTWRITEBYTECODE": "1",
            "QT_QPA_PLATFORM": "offscreen",
        }
    )
    result = subprocess.run(
        [str(launcher), "--working-directory", str(tmp_path), "--", str(probe)],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    # The package path must resolve before the optional PyQt import.  A missing
    # binding is an expected, explicit runtime error in this protocol-only test.
    assert result.returncode == 2, result.stderr
    assert "PyQt5 and the QTermWidget 1.4 SIP binding are required" in result.stderr


def test_pyqt_launcher_uses_only_adjacent_source_package(tmp_path: Path) -> None:
    source_root = product_layout(tmp_path) / "tools/ai/terminal/pyqt"
    launcher = _stage_test_terminal_launcher(source_root)
    _stage_test_terminal_package(source_root, 31)

    result = _run_test_terminal_launcher(launcher, cwd=tmp_path)

    assert result.returncode == 31


def test_pyqt_launcher_uses_only_same_release_installed_package(tmp_path: Path) -> None:
    installed_root = product_layout(tmp_path) / "tools/ai"
    launcher = _stage_test_terminal_launcher(installed_root / "bin")
    _stage_test_terminal_package(installed_root / "python", 32)

    result = _run_test_terminal_launcher(launcher, cwd=tmp_path)

    assert result.returncode == 32


def test_pyqt_launcher_explicit_root_does_not_fall_back_when_missing(
    tmp_path: Path,
) -> None:
    installed_root = product_layout(tmp_path) / "tools/ai"
    launcher = _stage_test_terminal_launcher(installed_root / "bin")
    _stage_test_terminal_package(installed_root / "python", 41)
    missing_root = tmp_path / "missing-frontend"

    result = _run_test_terminal_launcher(
        launcher,
        cwd=tmp_path,
        pyqt_root=str(missing_root),
    )

    assert result.returncode == 2
    assert f"SICO_AI_PYQT_ROOT is missing cad_ai_terminal: {missing_root}" in result.stderr


def test_pyqt_launcher_explicit_root_does_not_fall_back_when_incomplete(
    tmp_path: Path,
) -> None:
    installed_root = product_layout(tmp_path) / "tools/ai"
    launcher = _stage_test_terminal_launcher(installed_root / "bin")
    _stage_test_terminal_package(installed_root / "python", 41)
    configured_root = tmp_path / "configured-frontend"
    configured_package = _stage_test_terminal_package(configured_root, 42)
    (configured_package / "terminal_session.py").unlink()

    result = _run_test_terminal_launcher(
        launcher,
        cwd=tmp_path,
        pyqt_root=str(configured_root),
    )

    assert result.returncode == 2
    assert f"SICO_AI_PYQT_ROOT is incomplete at {configured_root}" in result.stderr
    assert "missing: cad_ai_terminal/terminal_session.py" in result.stderr


def test_pyqt_launcher_rejects_relative_explicit_root(tmp_path: Path) -> None:
    source_root = product_layout(tmp_path) / "tools/ai/terminal/pyqt"
    launcher = _stage_test_terminal_launcher(source_root)
    _stage_test_terminal_package(source_root, 41)

    result = _run_test_terminal_launcher(
        launcher,
        cwd=tmp_path,
        pyqt_root="relative/frontend",
    )

    assert result.returncode == 2
    assert "SICO_AI_PYQT_ROOT must be absolute" in result.stderr


def test_pyqt_launcher_explicit_complete_root_wins_over_adjacent_package(
    tmp_path: Path,
) -> None:
    installed_root = product_layout(tmp_path) / "tools/ai"
    launcher = _stage_test_terminal_launcher(installed_root / "bin")
    _stage_test_terminal_package(installed_root / "python", 41)
    configured_root = tmp_path / "configured-frontend"
    _stage_test_terminal_package(configured_root, 42)

    result = _run_test_terminal_launcher(
        launcher,
        cwd=tmp_path,
        pyqt_root=str(configured_root),
    )

    assert result.returncode == 42
