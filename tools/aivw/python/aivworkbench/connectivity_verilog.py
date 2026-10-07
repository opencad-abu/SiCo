"""Parse the bounded SI Verilog subset without inventing or repairing connections."""

from __future__ import annotations

import re

from .connectivity_lex import (
    canonical_expr,
    split_tokens,
    strip_comments,
    unescape_identifier,
)
from .connectivity_model import ConnectivityParseError, Instance, Module, Port


_IDENT = r"(?:\\[^\s,()]+\s|[A-Za-z_$][A-Za-z0-9_$]*)"


_MODULE_RE = re.compile(
    rf"\bmodule\s+(?P<name>{_IDENT})\s*\((?P<header>.*?)\)\s*;(?P<body>.*?)\bendmodule\b",
    re.IGNORECASE | re.DOTALL,
)


_SPECIFY_RE = re.compile(r"\bspecify\b.*?\bendspecify\b", re.IGNORECASE | re.DOTALL)


_DECL_RE = re.compile(
    r"\b(?P<direction>input|output|inout)\b(?:\s+wire|\s+logic|\s+reg|\s+tri|\s+real|\s+signed|\s+\[[^]]+\])*\s+(?P<names>[^;]+);",
    re.IGNORECASE,
)


_INSTANCE_RE = re.compile(
    rf"(?m)(?:^|(?<=;))\s*(?P<master>{_IDENT})\s+(?P<name>{_IDENT})\s*\((?P<args>.*?)\)\s*;",
    re.DOTALL,
)


def parse_verilog(text: str) -> tuple[Module, ...]:
    """Parse modules and top-level instance bindings from Verilog/SV text."""
    cleaned = strip_comments(text)
    modules: list[Module] = []
    for match in _MODULE_RE.finditer(cleaned):
        name = unescape_identifier(match.group("name"))
        header = match.group("header")
        body = _SPECIFY_RE.sub(" ", match.group("body"))
        ports = _parse_ports(header, body)
        instances: list[Instance] = []
        for item in _INSTANCE_RE.finditer(body):
            master = unescape_identifier(item.group("master"))
            instance = unescape_identifier(item.group("name"))
            args = item.group("args")
            tokens = split_tokens(args)
            named_tokens = []
            for token in tokens:
                named_match = re.fullmatch(
                    rf"\s*\.({_IDENT})\s*\(\s*(.*?)\s*\)\s*",
                    token,
                    re.DOTALL,
                )
                if named_match:
                    named_tokens.append(
                        (
                            unescape_identifier(named_match.group(1)),
                            canonical_expr(named_match.group(2)),
                        )
                    )
                elif named_tokens:
                    # Mixed positional/named syntax is ambiguous for this gate.
                    raise ConnectivityParseError(
                        f"mixed named/positional connections for {instance}"
                    )
            if named_tokens:
                if len(named_tokens) != len(tokens):
                    raise ConnectivityParseError(
                        f"mixed named/positional connections for {instance}"
                    )
                connections = tuple(named_tokens)
            else:
                connections = tuple(
                    (str(index), canonical_expr(value))
                    for index, value in enumerate(tokens)
                )
            instances.append(Instance(instance, master, connections))
        instance_names = [item.name for item in instances]
        if len(instance_names) != len(set(instance_names)):
            raise ConnectivityParseError(f"duplicate instance name in module {name}")
        modules.append(Module(name, tuple(ports), tuple(instances)))
    if not modules:
        raise ConnectivityParseError("no module declaration found")
    names = [module.name for module in modules]
    if len(names) != len(set(names)):
        raise ConnectivityParseError("candidate contains duplicate module declarations")
    return tuple(modules)


def _parse_ports(header: str, body: str) -> tuple[Port, ...]:
    declarations: dict[str, str] = {}
    for match in _DECL_RE.finditer(body):
        for name in split_tokens(match.group("names")):
            name = name.split("=")[0].strip()
            if name:
                declarations[unescape_identifier(name)] = match.group(
                    "direction"
                ).lower()
    ports: list[Port] = []
    for token in split_tokens(header):
        token = token.strip()
        if not token:
            continue
        direction = ""
        direction_match = re.match(r"^(input|output|inout)\b", token, re.IGNORECASE)
        if direction_match:
            direction = direction_match.group(1).lower()
            token = token[direction_match.end() :].strip()
        token = re.sub(
            r"^(?:wire|logic|reg|tri|real|signed)\b\s*", "", token, flags=re.IGNORECASE
        )
        token = re.sub(r"^\[[^]]+\]\s*", "", token)
        name = unescape_identifier(token)
        if name and name not in {"input", "output", "inout"}:
            ports.append(Port(name, direction or declarations.get(name, "")))
    # SI emits non-ANSI headers and declarations after the header.
    if not ports and declarations:
        ports = [
            Port(name, declarations.get(name, "")) for name in split_tokens(header)
        ]
    return tuple(ports)
