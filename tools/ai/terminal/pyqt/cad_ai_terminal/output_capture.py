"""Append opt-in terminal output used by private source acceptance probes."""

from __future__ import annotations

from pathlib import Path


def _append_test_output(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as output:
        output.write(text)
