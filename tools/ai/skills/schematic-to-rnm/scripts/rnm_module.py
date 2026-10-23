"""Conservative SystemVerilog module facts for private RNM checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

LINE_COMMENT_RE = re.compile(r"//[^\n]*")

MODULE_RE = re.compile(
    r"\bmodule\s+(\w+)\s*(?:#\s*\((?P<params>.*?)\))?\s*\((?P<ports>.*?)\)\s*;",
    re.DOTALL,
)

DIRECTION_RE = re.compile(
    r"\b(input|output|inout)\b(?P<body>[^;]*);",
    re.DOTALL,
)

NET_TYPE_RE = re.compile(
    r"\b(wreal|wreal1driver|wreal4state|wrealsum|wrealmin|wrealmax|wrealavg|"
    r"real|logic|wire|reg|bit)\b",
)

NET_DECL_RE = re.compile(
    r"^\s*(wreal1driver|wreal4state|wrealsum|wrealmin|wrealmax|wrealavg|wreal)\b"
    r"(?P<body>[^;]*);",
    re.MULTILINE | re.DOTALL,
)

PARAM_DECL_RE = re.compile(
    r"\bparameter\s+(?:real\s+|int\s+|integer\s+)?(\w+)\s*=",
)

# SystemVerilog escaped identifiers begin with ``\\`` and continue through the
# next whitespace delimiter.  OA commonly emits names such as ``Din+`` and
# ``Din-``; accepting them here keeps the checker aligned with the schematic
# interface instead of silently truncating them to ``Din``.
IDENT_RE = re.compile(r"\\[^\s,;()]+|[A-Za-z_]\w*")


def strip_comments(text: str) -> str:
    return LINE_COMMENT_RE.sub("", BLOCK_COMMENT_RE.sub(" ", text))


def declared_identifiers(body: str) -> list[str]:
    """Identifiers in a declaration body, minus type/range tokens."""

    without_ranges = re.sub(r"\[[^\]]*\]", " ", body)
    names: list[str] = []
    for match in IDENT_RE.finditer(without_ranges):
        token = match.group(0)
        if token.startswith("\\"):
            token = token[1:]
        if NET_TYPE_RE.fullmatch(token) or token in {"signed", "unsigned", "var"}:
            continue
        names.append(token)
    return names


@dataclass
class ModuleFacts:
    name: str
    header_ports: list[str]
    directions: dict[str, str]
    net_types: dict[str, str]
    parameters: list[str]
    ansi_header: bool

    def port_names(self) -> list[str]:
        if self.ansi_header:
            return list(self.header_ports)
        # Non-ANSI: the header lists names, directions come from the body.
        ordered = list(self.header_ports)
        for name in self.directions:
            if name not in ordered:
                ordered.append(name)
        return ordered


def parse_module(text: str) -> Optional[ModuleFacts]:
    clean = strip_comments(text)
    match = MODULE_RE.search(clean)
    if not match:
        return None

    ports_text = match.group("ports") or ""
    ansi_header = bool(re.search(r"\b(input|output|inout)\b", ports_text))

    header_ports: list[str] = []
    directions: dict[str, str] = {}
    net_types: dict[str, str] = {}

    if ansi_header:
        current_direction = ""
        current_type = ""
        for chunk in ports_text.split(","):
            direction_match = re.search(r"\b(input|output|inout)\b", chunk)
            if direction_match:
                current_direction = direction_match.group(1)
            type_match = NET_TYPE_RE.search(chunk)
            if type_match:
                current_type = type_match.group(1)
            names = [
                name
                for name in declared_identifiers(
                    re.sub(r"\b(input|output|inout)\b", " ", chunk)
                )
                if name
            ]
            for name in names:
                header_ports.append(name)
                if current_direction:
                    directions[name] = current_direction
                if current_type:
                    net_types[name] = current_type
    else:
        header_ports = [name for name in declared_identifiers(ports_text)]

    body = clean[match.end() :]
    for decl in DIRECTION_RE.finditer(body):
        direction = decl.group(1)
        for name in declared_identifiers(decl.group("body")):
            directions.setdefault(name, direction)
            type_match = NET_TYPE_RE.search(decl.group("body"))
            if type_match:
                net_types.setdefault(name, type_match.group(1))

    for decl in NET_DECL_RE.finditer(body):
        net_type = decl.group(1)
        for name in declared_identifiers(decl.group("body")):
            net_types[name] = net_type

    parameters = [match.group(1) for match in PARAM_DECL_RE.finditer(clean)]

    return ModuleFacts(
        name=match.group(1),
        header_ports=header_ports,
        directions=directions,
        net_types=net_types,
        parameters=parameters,
        ansi_header=ansi_header,
    )
