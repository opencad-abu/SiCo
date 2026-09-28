"""Safe text transformations for generated parasitic netlists."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .config import RceConfig
from .textutil import write_text


@dataclass(frozen=True)
class BusDelimiterMapping:
    source: str
    target: str

    @property
    def calibre_character_map(self) -> str:
        return "".join(
            source + target
            for source, target in zip(self.source, self.target)
        )


_BUS_DELIMITER_MAPPINGS = {
    "<> ===> []": BusDelimiterMapping("<>", "[]"),
    "[] ===> <>": BusDelimiterMapping("[]", "<>"),
}

_HEADER_RE = re.compile(
    r"^(?P<prefix>\s*\*(?:\|BUSBIT|BUS_DELIMITER)\s+)"
    r"(?P<quote>['\"]?)(?P<delimiter><\s*>|\[\s*\])(?P=quote)",
    re.IGNORECASE,
)
_ANGLE_BUS_RE = re.compile(
    r"(?P<lead>[^\s<>=])<(?P<index>[A-Za-z0-9_:+*,-]+)>"
)
_SQUARE_BUS_RE = re.compile(
    r"(?P<lead>[^\s\[\]=])\[(?P<index>[A-Za-z0-9_:+*,-]+)\]"
)
_QUOTED_TEXT_RE = re.compile(
    r'"(?:\\.|[^"\\])*"'
    r"|'(?:\\.|[^'\\])*'"
)
_METADATA_PREFIXES = (
    ".title",
    "*comment",
    "*design_flow",
    "*date",
    "*program",
    "*vendor",
    "*version",
)


def configured_bus_delimiter_mapping(
    cfg: RceConfig,
) -> BusDelimiterMapping | None:
    if not cfg.flag(
        "netlist", "brackets_replace"
    ):
        return None

    raw_type = cfg.text(
        "netlist",
        "brackets_replace_type",
    ).strip()
    try:
        return _BUS_DELIMITER_MAPPINGS[raw_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported brackets replacement type: {raw_type!r}") from exc


def map_netlist_bus_delimiters(
    text: str, mapping: BusDelimiterMapping
) -> str:
    return "".join(_map_line(line, mapping) for line in text.splitlines(keepends=True))


def rewrite_netlist_bus_delimiters(
    path: Path, mapping: BusDelimiterMapping
) -> Path | None:
    original = path.read_text(encoding="utf-8", errors="replace")
    transformed = map_netlist_bus_delimiters(original, mapping)
    if transformed == original:
        return None

    backup = path.with_name(f"{path.name}.pre_brackets")
    temporary = path.with_name(f".{path.name}.brackets.{os.getpid()}")
    shutil.copy2(path, backup)
    try:
        write_text(temporary, transformed)
        temporary.chmod(path.stat().st_mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return backup


def _map_line(line: str, mapping: BusDelimiterMapping) -> str:
    header = _HEADER_RE.match(line)
    if header:
        start, end = header.span("delimiter")
        return line[:start] + mapping.target + line[end:]

    stripped = line.lstrip()
    lowered = stripped.casefold()
    if (
        stripped == "*"
        or stripped.startswith(("* ", "**", "//"))
        or lowered.startswith(_METADATA_PREFIXES)
    ):
        return line

    code, suffix = _split_inline_comment(line)
    return _map_unquoted_code(code, mapping) + suffix


def _split_inline_comment(line: str) -> tuple[str, str]:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(line):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif line.startswith("//", index) or (
            character in {"$", ";"}
            and (index == 0 or line[index - 1].isspace())
        ):
            return line[:index], line[index:]
    return line, ""


def _map_unquoted_code(code: str, mapping: BusDelimiterMapping) -> str:
    chunks: list[str] = []
    position = 0
    for quoted in _QUOTED_TEXT_RE.finditer(code):
        chunks.append(_map_code_tokens(code[position : quoted.start()], mapping))
        chunks.append(quoted.group(0))
        position = quoted.end()
    chunks.append(_map_code_tokens(code[position:], mapping))
    return "".join(chunks)


def _map_code_tokens(code: str, mapping: BusDelimiterMapping) -> str:
    parts = re.split(r"(\s+)", code)
    for index, token in enumerate(parts):
        if not token or token.isspace() or "=" in token:
            continue
        parts[index] = _map_identifier_token(token, mapping)
    return "".join(parts)


def _map_identifier_token(token: str, mapping: BusDelimiterMapping) -> str:
    if mapping.source == "<>":
        return _ANGLE_BUS_RE.sub(
            lambda match: (
                f"{match.group('lead')}[{match.group('index')}]"
            ),
            token,
        )
    return _SQUARE_BUS_RE.sub(
        lambda match: f"{match.group('lead')}<{match.group('index')}>",
        token,
    )
