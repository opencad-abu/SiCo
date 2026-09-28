"""Bounded model corner cache for repeated GUI cell/dialect selections."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re

_SECTION = re.compile(r"section\s+([A-Za-z_][A-Za-z0-9_$.-]*)", re.IGNORECASE)
_SECTION_OPTION = re.compile(
    r"section\s*=\s*([A-Za-z_][A-Za-z0-9_$.-]*)", re.IGNORECASE
)
_LIB = re.compile(r"\.lib\b", re.IGNORECASE)
_TOKENS = re.compile(r"(?:'[^']*'|\"[^\"]*\"|[^\s]+)")


def model_corners(path: str) -> tuple[str, ...]:
    """Reuse parsed names until the file is changed or atomically replaced."""
    try:
        source = Path(path).expanduser().resolve()
        stat = source.stat()
        identity = (
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        )
        return _scan(source, identity)
    except OSError:
        # Missing/unreadable files are retried on the next selection.
        return ()


@lru_cache(maxsize=128)
def _scan(source: Path, identity: tuple[int, ...]) -> tuple[str, ...]:
    names = []
    seen = set()
    # Stream potentially large PDK files; retain only corner names.
    with source.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            stripped = line.strip()
            match = _SECTION.match(stripped)
            if match:
                name = match.group(1)
            elif _LIB.match(stripped):
                body = stripped[4:].strip().split("$", 1)[0].split(";", 1)[0]
                tokens = _TOKENS.findall(body)
                name = tokens[-1].strip("'\"") if tokens else ""
            else:
                match = _SECTION_OPTION.match(stripped)
                if not match:
                    continue
                name = match.group(1)
            if name and name.casefold() not in seen:
                names.append(name)
                seen.add(name.casefold())
    return tuple(names)
