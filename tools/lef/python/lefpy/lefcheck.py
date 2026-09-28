"""Focused structural validation for Abstract Generator LEF output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_VERSION = re.compile(r"^VERSION\s+([^\s;]+)\s*;\s*$", re.IGNORECASE)
_MACRO = re.compile(r"^MACRO\s+(\S+)\s*$", re.IGNORECASE)
_END = re.compile(r"^END\s+(\S+)\s*$", re.IGNORECASE)
_END_LIBRARY = re.compile(r"^END\s+LIBRARY\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class LefSummary:
    version: str
    macros: tuple[str, ...]


def inspect_lef(path: Path) -> LefSummary:
    text = path.read_text(encoding="utf-8", errors="replace")
    if "\x00" in text:
        raise ValueError("LEF output contains a NUL byte")

    version: str | None = None
    macros: list[str] = []
    current_macro: str | None = None
    macro_section_started = False
    library_ended = False

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if library_ended:
            raise ValueError(f"Unexpected content after END LIBRARY at line {line_number}")

        match = _VERSION.fullmatch(line)
        if match:
            if version is not None:
                raise ValueError(f"Duplicate VERSION statement at line {line_number}")
            if macro_section_started:
                raise ValueError(f"VERSION appears after MACRO data at line {line_number}")
            version = match.group(1)
            continue

        if _END_LIBRARY.fullmatch(line):
            if current_macro is not None:
                raise ValueError(f"MACRO {current_macro} is missing its END statement")
            library_ended = True
            continue

        match = _MACRO.fullmatch(line)
        if match:
            if version is None:
                raise ValueError(f"MACRO appears before VERSION at line {line_number}")
            if current_macro is not None:
                raise ValueError(f"MACRO {current_macro} is missing its END statement")
            name = match.group(1)
            if name in macros:
                raise ValueError(f"Duplicate MACRO in LEF output: {name}")
            macros.append(name)
            current_macro = name
            macro_section_started = True
            continue

        if current_macro is not None:
            match = _END.fullmatch(line)
            if match and match.group(1) == current_macro:
                current_macro = None
            continue

        if macro_section_started:
            raise ValueError(f"Unexpected content between MACRO blocks at line {line_number}")

    if version is None:
        raise ValueError("LEF output is missing VERSION")
    if current_macro is not None:
        raise ValueError(f"MACRO {current_macro} is missing its END statement")
    if not library_ended:
        raise ValueError("LEF output is missing END LIBRARY")
    return LefSummary(version=version, macros=tuple(macros))


def validate_lef(
    path: Path,
    *,
    expected_version: str,
    expected_cells: Iterable[str],
) -> LefSummary:
    summary = inspect_lef(path)
    if summary.version != expected_version:
        raise ValueError(
            f"LEF version mismatch: expected {expected_version}, got {summary.version}"
        )

    expected = set(expected_cells)
    actual = set(summary.macros)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected: {', '.join(unexpected)}")
        raise ValueError(f"LEF MACRO set mismatch ({'; '.join(details)})")
    return summary
