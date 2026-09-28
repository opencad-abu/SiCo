from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
COMMON_PYTHON = ROOT.parent / "common" / "python"
MTS_PYTHON = ROOT / "python"
for path in (COMMON_PYTHON, MTS_PYTHON):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


@pytest.fixture(autouse=True)
def isolated_project_modules(tmp_path, monkeypatch):
    """Keep project discovery independent of the developer's site setup."""
    root = tmp_path / "configured-modulefiles"
    root.mkdir()
    monkeypatch.setenv("MTS_NETLISTOR_MODULEFILES", str(root))


@pytest.fixture
def protected_worker_context(tmp_path, monkeypatch):
    """Provide a path marker only for tests with a mocked EDA execution boundary."""
    tools = tmp_path / "runtime"
    (tools / "context").mkdir(parents=True)
    (tools / "context/cadWorkers.cxt").touch()
    monkeypatch.setenv("CAD_RUNTIME_TOOLS", str(tools))
    return tools
