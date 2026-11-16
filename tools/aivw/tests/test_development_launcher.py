"""Interpreter selection for the development launcher, without source bundles."""

import json
import os
from pathlib import Path
import subprocess

import pytest


TOOL_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = TOOL_ROOT.parents[1]
LAUNCHER = TOOL_ROOT / "bin/aivw"


@pytest.fixture(scope="module")
def configured_python():
    interpreter = PRODUCT_ROOT / "runtime/python/bin/python3"
    if not interpreter.is_file():
        pytest.skip("$SICO_HOME/runtime/python is required for launcher checks")
    return PRODUCT_ROOT, interpreter


def launcher_environment(configured_python):
    root, interpreter = configured_python
    return {
        **{key: value for key, value in os.environ.items() if key not in {"CAD_HOME", "SICO_HOME", "CAD_PYTHON_ROOT", "CAD_PYTHON"}},
        "SICO_HOME": str(root),
        "SICO_PYTHON": "/stale/python3",
        "SICO_PYTHON_ROOT": "/stale/python",
        "PYTHONPATH": "/untrusted/pythonpath",
        "PYTHONHOME": "/untrusted/pythonhome",
        "PYTHONNOUSERSITE": "0",
        "LD_PRELOAD": "/untrusted/preload.so",
        "LD_AUDIT": "untrusted-audit.so",
    }


def test_launcher_clears_ambient_interpreter_settings(tmp_path, configured_python):
    for arguments in (["--version"], ["runtime-info", "--json"]):
        result = subprocess.run(
            [str(LAUNCHER), *arguments], cwd=tmp_path,
            env=launcher_environment(configured_python), capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        if arguments == ["--version"]:
            assert "aivw " in result.stdout
        else:
            report = json.loads(result.stdout)
            assert report["version_exact"]
            assert report["CAD_PYTHON_matches_root_entry"]


def test_launcher_ignores_stale_python_selectors(tmp_path, configured_python):
    root, interpreter = configured_python
    environment = launcher_environment(configured_python)
    result = subprocess.run(
        [str(LAUNCHER), "runtime-info", "--json"], cwd=tmp_path,
        env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["CAD_PYTHON"] == str(interpreter)
    assert report["CAD_PYTHON_ROOT"] == str(root / "runtime/python")


def test_launcher_cannot_import_a_working_directory_package(tmp_path, configured_python):
    impostor = tmp_path / "aivworkbench"
    impostor.mkdir()
    (impostor / "__init__.py").write_text("raise RuntimeError(\"untrusted CWD package\")\n")
    result = subprocess.run([str(LAUNCHER), "--version"], cwd=tmp_path,
                            env=launcher_environment(configured_python),
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "aivw " in result.stdout
