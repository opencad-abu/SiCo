"""Run source GUI probes against the existing PyQt5/QTermWidget runtime."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from terminal_fixtures import PYQT_ROOT


def run_real_terminal_smoke(platform: str) -> None:
    python = Path("/software/pkgs/python/3.9.13/bin/python3")
    runtime = PYQT_ROOT.parents[2] / ".cad" / "sip1211-runtime-rhel8"
    binding = runtime / "python" / "QTermWidget.abi3.so"
    library = runtime / "lib" / "libqtermwidget5.so.1"
    resources = PYQT_ROOT.parents[1] / "share" / "qtermwidget5"
    if not all(path.exists() for path in (python, binding, library, resources)):
        pytest.skip("production PyQt5/QTermWidget smoke runtime is unavailable")

    script = (Path(__file__).parent / "terminal_smoke/run.py").read_text()
    environment = os.environ.copy()
    environment.update(
        {
            "CAD_AI_QTERMWIDGET_DATA_PATH": str(resources),
            "LD_LIBRARY_PATH": os.pathsep.join(
                (str(runtime / "lib"), str(python.parents[1] / "lib"))
            ),
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": os.pathsep.join(
                (str(runtime / "python"), str(PYQT_ROOT), str(Path(__file__).parent))
            ),
            "QT_QPA_PLATFORM": platform,
        }
    )

    completed = subprocess.run(
        [str(python), "-c", script],
        cwd=PYQT_ROOT.parents[2],
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
