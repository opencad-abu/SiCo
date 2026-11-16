"""Isolated Git worktrees for audit-gate behavior tests."""

import json
import sys
from pathlib import Path
import subprocess

import pytest

# Utility tools are plain modules: make them importable from the tests.
UTILITY_ROOT = Path(__file__).resolve().parents[1]
if str(UTILITY_ROOT) not in sys.path:
    sys.path.insert(0, str(UTILITY_ROOT))
if str(UTILITY_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(UTILITY_ROOT.parent))

from utility.tests.gate_fixtures import write


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


@pytest.fixture
def closure_repo(repo):
    def make(files, *, registered=None, entries=None, complete=None, development=None):
        for path, content in files.items():
            write(repo, "src/" + path, content)
        if registered is None:
            registered = list(files)
        modules = [
            {
                "name": ".".join(Path(path).with_suffix("").parts),
                "source": path,
            }
            for path in registered
        ]
        write(
            repo,
            "native.json",
            json.dumps(
                dict(format="cad.python.native.v2", source_root="src", modules=modules,
                     closure=dict(search_paths=["."], entries=entries or ["pkg.main"],
                                  complete_packages=complete or [],
                                  development_modules=development or []))
            ),
        )
        return dict(schema_version=2, inventories=["native.json"])

    return repo, make
