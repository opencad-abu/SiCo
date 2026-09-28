"""Run opt-in Virtuoso source probes with isolated Python/Qt environment."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


def run_virtuoso_source(
    source: str,
    cwd: Path,
    *,
    env_updates: dict[str, str] | None = None,
    log_path: Path | None = None,
) -> str:
    executable = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    if executable is None:
        pytest.skip("virtuoso is unavailable")
    env = os.environ.copy()
    for name in (
        "QT_QPA_PLATFORM", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
        "LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME",
    ):
        env.pop(name, None)
    env.update(env_updates or {})
    command = [executable, "-nograph", "-nocdsinit"]
    if log_path is not None:
        command.extend(["-log", str(log_path)])
    # Regular files avoid waiting on pipes inherited by Cadence helper processes.
    # Newline-terminated stdin also avoids replay scheduling in a headless session.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        completed = subprocess.run(
            command, input=source.rstrip() + "\n", text=True,
            stdout=log, stderr=subprocess.STDOUT, cwd=cwd, env=env,
            timeout=60, check=False,
        )
        log.seek(0)
        output = log.read()
    if log_path is not None and log_path.is_file():
        output += log_path.read_text(encoding="utf-8", errors="replace")
    assert completed.returncode == 0, output
    assert "*Error*" not in output, output
    assert "(reader)" not in output, output
    assert "still unclosed on EOF" not in output, output
    return output
