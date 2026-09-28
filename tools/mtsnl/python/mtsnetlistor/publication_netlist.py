"""Stage top names and Spectre language markers without mutating stable input."""

from __future__ import annotations

import re
from .errors import RequestValidationError


def rename_top_netlist(text: str, dialect: str, source_cell: str, target_cell: str) -> str:
    """Rename only the selected top declaration and matching terminator."""

    if source_cell == target_cell:
        return text
    lines = text.splitlines()
    start_prefix = "subckt" if dialect == "spectre" else ".subckt"
    end_prefix = "ends" if dialect == "spectre" else ".ends"
    starts: list[int] = []
    for index, line in enumerate(lines):
        fields = line.strip().split()
        if len(fields) >= 2 and fields[0].casefold() == start_prefix.casefold() and fields[1] == source_cell:
            starts.append(index)
    if len(starts) != 1:
        raise RequestValidationError(f"cannot uniquely rename top {source_cell!r}: found {len(starts)} declarations")
    start = starts[0]
    fields = lines[start].split()
    fields[1] = target_cell
    indentation = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
    lines[start] = indentation + " ".join(fields)
    depth = 0
    for index in range(start + 1, len(lines)):
        fields = lines[index].strip().split()
        if not fields:
            continue
        if fields[0].casefold() == start_prefix.casefold():
            depth += 1
        elif fields[0].casefold() == end_prefix.casefold():
            if depth:
                depth -= 1
                continue
            if len(fields) >= 2 and fields[1] == source_cell:
                fields[1] = target_cell
                indentation = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
                lines[index] = indentation + " ".join(fields)
                return "\n".join(lines).rstrip() + "\n"
            raise RequestValidationError(f"top {source_cell!r} terminator is missing or mismatched")
    raise RequestValidationError(f"top {source_cell!r} terminator is missing")


def _ensure_spectre_language(text: str) -> str:
    """Ensure a staged Spectre deck selects the Spectre parser.

    Cadence's ``cdsTextTo5x`` uses the input suffix and language directives
    together.  Stable MTS artifacts are intentionally named ``.spe`` for
    compatibility, while the publication staging file uses ``.scs`` on
    IC23.10.  Older/generated decks can still lack a language marker, so add
    one only to the private staging copy.  Existing directives, including an
    intentional embedded ``simulator lang=spice`` switch, are preserved.
    """

    if re.search(
        r"(?im)^\s*simulator\s+lang\s*=\s*spectre\b",
        text,
    ):
        return text
    return "simulator lang=spectre\n" + text
