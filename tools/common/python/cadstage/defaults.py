"""Validate literal CDL and stream-out defaults without executing them."""

from __future__ import annotations

import re
import shlex

from caddefaults.source import Source
from caddefaults.syntax import sections


_CDL_RESERVED = {
    "simLibName", "simCellName", "simViewName", "simSimulator",
    "hnlNetlistFileName", "incFILE", "auCdlReplaceAngleBracketsWithSquare",
}
_GDS_RESERVED = {
    "library", "topcell", "view", "layermap", "strmfile", "logfile",
    "outputdir", "rundir", "replacebusbitchar",
}
_LITERAL = re.compile(r'\s*("(?:[^"\\]|\\.)*"|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|nil\b|t\b|list\b|[()\x27])')


def _literal(text: str) -> bool:
    tokens = []
    position = 0
    while position < len(text):
        match = _LITERAL.match(text, position)
        if not match:
            return False
        tokens.append(match[1])
        position = match.end()

    def value(index):
        if index >= len(tokens):
            raise ValueError
        if tokens[index] == "'":
            return value(index + 1)
        if tokens[index] == "(":
            index += 1
            if index < len(tokens) and tokens[index] == "list":
                index += 1
            while index < len(tokens) and tokens[index] != ")":
                index = value(index)
            if index >= len(tokens):
                raise ValueError
            return index + 1
        if tokens[index] in {")", "list"}:
            raise ValueError
        return index + 1

    try:
        return bool(tokens) and value(0) == len(tokens)
    except ValueError:
        return False


def cdl_lines(source: Source) -> dict[str, str]:
    groups = sections(source, {"netlisting", "devices"}, comments=(";", "#"))
    seen = set()
    for number, line in (row for records in groups.values() for row in records):
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.+)", line)
        if not match or not _literal(match[2]):
            raise source.error(number, "Expected a CDL literal assignment; no executable SKILL")
        name = match[1]
        if name in _CDL_RESERVED or name in seen:
            raise source.error(number, f"Reserved or repeated CDL setting {name}")
        seen.add(name)
    return {name: "\n".join(line for _, line in records) for name, records in groups.items()}


def streamout_lines(source: Source) -> dict[str, str]:
    groups = sections(source, {"geometry", "vertices", "pins", "format"})
    seen = set()
    for number, line in (row for records in groups.values() for row in records):
        try:
            tokens = shlex.split(line)
        except ValueError as exc:
            raise source.error(number, str(exc)) from exc
        if len(tokens) != 2 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", tokens[0]):
            raise source.error(number, "Expected one stream-out option and one value")
        name = tokens[0].lower()
        if name in _GDS_RESERVED or name in seen or name in {"include", "templatefile"}:
            raise source.error(number, f"Reserved or repeated stream-out setting {tokens[0]}")
        seen.add(name)
    return {name: "\n".join(line for _, line in records) for name, records in groups.items()}
