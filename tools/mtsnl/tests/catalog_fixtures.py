"""Fake catalog protocol executables and isolated source fixtures."""

from __future__ import annotations
import json
from pathlib import Path
import sys


def _write_dbaccess(path: Path, library: str, physical: Path) -> Path:
    payload = json.dumps(
        {
            "schema_version": 1,
            "authoritative": True,
            "libraries": [
                {
                    "name": library,
                    "path": str(physical),
                    "writable": True,
                    "cells": [],
                }
            ],
        }
    )
    path.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        f"print({payload!r})\n"
        f"open(os.environ['CADVIEW_CATALOG_OUTPUT'], 'w', encoding='utf-8').write({payload!r})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _counting_dbaccess(path: Path, counter: Path, *, delay: float = 0.0, fail: bool = False) -> Path:
    payload = json.dumps(
        {
            "schema_version": 1,
            "authoritative": True,
            "libraries": [],
        }
    )
    script = (
        f"#!{sys.executable}\n"
        "import json, os, time\n"
        f"counter={str(counter)!r}\n"
        "with open(counter, 'a', encoding='utf-8') as handle: handle.write('1')\n"
        f"time.sleep({delay!r})\n"
        + ("raise SystemExit(7)\n" if fail else "")
        + f"payload={payload!r}\n"
        "print(payload)\n"
        "open(os.environ['CADVIEW_CATALOG_OUTPUT'], 'w', encoding='utf-8').write(payload)\n"
    )
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)
    return path


def _source_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    included = tmp_path / "included.cds.lib"
    library = tmp_path / "library"
    library.mkdir()
    included.write_text("DEFINE work ./library\n", encoding="utf-8")
    source = tmp_path / "source.cds.lib"
    source.write_text("INCLUDE $CATALOG_INCLUDE\n", encoding="utf-8")
    counter = tmp_path / "calls"
    return source, included, counter
