"""Isolated Git worktrees for audit-gate behavior tests."""

import json
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def write(root, path, text=""):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def commit(root, *paths):
    subprocess.run(["git", "add", "--", *paths], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Gate Test",
            "-c",
            "user.email=gate@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=root,
        check=True,
    )


@pytest.fixture
def closure_repo(repo):
    def make(files, *, registered=None, entries=None, complete=None):
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
                dict(format="cad.python.native.v1", source_root="src", modules=modules)
            ),
        )
        spec = dict(
            path="native.json",
            search_paths=["src"],
            entries=entries or ["pkg.main"],
            complete_packages=complete or [],
            development_entries=[],
        )
        return dict(schema_version=1, inventories=[spec])

    return repo, make
