from __future__ import annotations

import os
import sys

import pytest
from sico import interpreter


def test_native_launcher_binary_is_executed_directly(monkeypatch, tmp_path):
    """A packaged ELF launcher selects CAD_PYTHON itself; never wrap it in Python."""
    launcher = tmp_path / "bin" / "sico"
    launcher.parent.mkdir()
    launcher.write_bytes(b"\x7fELF\x02\x01\x01\x00" + bytes(64))
    launcher.chmod(0o755)
    monkeypatch.setattr(interpreter, "native_entry", lambda name: launcher)
    assert interpreter.agent_command("desktop-worker", "--session", "probe") == [
        str(launcher),
        "desktop-worker",
        "--session",
        "probe",
    ]
    assert os.access(launcher, os.X_OK)


def test_source_launcher_requires_installation_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("SICO_PYTHON", sys.executable)
    monkeypatch.setenv("SICO_PYTHON_ROOT", str(tmp_path / "unavailable"))
    with pytest.raises(
        ValueError, match=r"\$SICO_HOME/runtime/python/bin/python3 is unavailable"
    ):
        interpreter.agent_command("desktop-worker")


def test_bundled_runtime_ignores_stale_site_python_selectors(monkeypatch, tmp_path):
    bundled = tmp_path / "runtime/python/bin/python3"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("#!/bin/sh\n", encoding="ascii")
    bundled.chmod(0o755)

    class Installation:
        runtime_python = bundled

    monkeypatch.setattr(interpreter, "current", lambda environment=None: Installation())
    assert interpreter.cad_python({
        "SICO_PYTHON": str(tmp_path / "stale/bin/python3"),
        "SICO_PYTHON_ROOT": str(tmp_path / "stale"),
    }) == str(bundled)


def test_unmarked_package_does_not_fall_back_to_module_search(monkeypatch, tmp_path):
    monkeypatch.setenv("SICO_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="installation identity"):
        interpreter.agent_command("desktop-worker")
