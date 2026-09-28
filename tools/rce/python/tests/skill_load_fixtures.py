from __future__ import annotations

import re
import subprocess
from pathlib import Path

CAD_ROOT = Path(__file__).resolve().parents[3]
FUNCTION_RE = re.compile(
    r"^\s*procedure\(\s*([A-Za-z_][A-Za-z0-9_]*)\(",
    re.MULTILINE,
)


def _skill_sources() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "*.il"],
        cwd=CAD_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    # The checked-out AIVW research tree is user-owned and intentionally has
    # independent SKILL loading/version rules; CAD production checks exclude it.
    return [
        CAD_ROOT / relative
        for relative in completed.stdout.splitlines()
        if not relative.startswith("aivw/")
    ]


def _procedure_body(text: str, name: str) -> str:
    start = text.index(f"procedure({name}(")
    end = text.find("\nprocedure(", start + 1)
    return text[start:] if end < 0 else text[start:end]
