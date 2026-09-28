from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from dspf_gui_test_support import write_small_dspf
from rcepy import cli


def test_dspf_index_runs_without_importing_qt_or_tomli(tmp_path: Path) -> None:
    command = """
import builtins
import sys

original_import = builtins.__import__

def blocked_import(name, *args, **kwargs):
    if name in {"tomli", "tomllib"}:
        raise ModuleNotFoundError(name)
    return original_import(name, *args, **kwargs)

builtins.__import__ = blocked_import
from rcepy.cli import main
status = main([
    "dspf-index", sys.argv[1], "--cache-dir", sys.argv[2], "--json",
])
print(
    "RUNTIME",
    status,
    any(name.startswith("PyQt5") for name in sys.modules),
    "rcepy.config" in sys.modules,
    "rcepy.runner" in sys.modules,
)
"""
    source = write_small_dspf(tmp_path / "small.dspf")
    cache = tmp_path / "cache"
    python_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(python_root) + (os.pathsep + existing if existing else "")
    completed = subprocess.run(
        [sys.executable, "-c", command, str(source), str(cache)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    lines = completed.stdout.splitlines()
    assert json.loads(lines[-2])["status"] == "complete"
    assert lines[-1] == "RUNTIME 0 False False False"


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["dspf-index", "input.dspf", "--force", "--json"], "dspf-index"),
        (["dspf-info", "index.sqlite3", "--json"], "dspf-info"),
        (["dspf-gui", "input.dspf", "--bridge"], "dspf-gui"),
    ],
)
def test_dspf_cli_contract(argv: list[str], expected: str) -> None:
    args = cli.build_parser().parse_args(argv)
    assert args.command == expected


def test_dspf_index_json_dispatch(monkeypatch, capsys, tmp_path: Path) -> None:
    result = SimpleNamespace(
        to_dict=lambda: {
            "source_path": str(tmp_path / "input.dspf"),
            "index_path": str(tmp_path / "index.sqlite3"),
            "reused": False,
        }
    )
    import rcepy.dspf.indexer as indexer

    calls = []
    monkeypatch.setattr(indexer, "build_index", lambda *args, **kwargs: calls.append((args, kwargs)) or result)
    assert cli.main(["dspf-index", "input.dspf", "--force", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["reused"] is False
    assert calls[0][1]["force"] is True


def test_dspf_gui_dispatch_is_lazy(monkeypatch) -> None:
    calls = []
    fake = SimpleNamespace(run_gui=lambda **kwargs: calls.append(kwargs) or 17)
    monkeypatch.setitem(sys.modules, "rcepy.dspf_gui.app", fake)
    assert cli.main(["dspf-gui", "input.dspf", "--bridge", "stdio"]) == 17
    assert calls == [
        {"source": "input.dspf", "cache_dir": None, "force": False, "bridge": True}
    ]


def test_dspf_cli_index_and_info_round_trip(tmp_path: Path, capsys) -> None:
    source = write_small_dspf(tmp_path / "small.dspf")
    cache = tmp_path / "cache"
    assert cli.main(["dspf-index", str(source), "--cache-dir", str(cache), "--json"]) == 0
    indexed = json.loads(capsys.readouterr().out)
    assert indexed["status"] == "complete"

    assert cli.main(["dspf-info", indexed["index_path"], "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["counts"]["net"] == 2
