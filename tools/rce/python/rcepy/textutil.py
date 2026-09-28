"""Text rendering helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


def ensure_dirs(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def q(value: str) -> str:
    return str(value).replace('"', '\\"')


def clean_lines(lines: Iterable[str]) -> str:
    return "\n".join(line.rstrip() for line in lines).rstrip() + "\n"
