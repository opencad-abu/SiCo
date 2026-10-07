"""Parse authoritative Cadence SI netlists and mapping metadata."""

from __future__ import annotations

import re
from typing import Iterable

from .connectivity_model import ConnectivityParseError, Module, Structure
from .connectivity_verilog import parse_verilog


_MAP_NET_RE = re.compile(r'^\s*\(\s*"([^"]+)"\s+"([^"]+)"\s*\)\s*$')


def parse_si_netlists(texts: Iterable[str]) -> Structure:
    modules: list[Module] = []
    for text in texts:
        modules.extend(parse_verilog(text))
    if not modules:
        raise ConnectivityParseError("official SI inventory contains no modules")
    names = [module.name for module in modules]
    if len(names) != len(set(names)):
        raise ConnectivityParseError("official SI inventory contains duplicate modules")
    return Structure(tuple(modules))


def parse_si_map(text: str) -> tuple[tuple[str, str], ...]:
    """Parse the net aliases from SI ``map/current``."""
    result: list[tuple[str, str]] = []
    in_net = False
    for raw in text.splitlines():
        line = raw.strip()
        if line == "( net" or line.endswith("( net"):
            in_net = True
            continue
        if in_net and line == ")":
            in_net = False
            continue
        if in_net:
            match = _MAP_NET_RE.match(line)
            if match:
                result.append((match.group(1), match.group(2)))
            elif line and not line.startswith("//"):
                raise ConnectivityParseError(f"unrecognized SI map net entry: {line}")
    if "( net" not in text and not any(
        line.strip().endswith("( net") for line in text.splitlines()
    ):
        raise ConnectivityParseError("SI map has no net section")
    by_source: dict[str, str] = {}
    for source, target in result:
        previous = by_source.get(source)
        if previous is not None and previous != target:
            raise ConnectivityParseError(
                f"SI map source alias {source!r} maps to conflicting targets"
            )
        by_source[source] = target
    return tuple(sorted(result))


def parse_globalmap(text: str) -> tuple[tuple[str, str], ...]:
    """Parse global aliases from ``ihnl/globalmap``."""
    result: list[tuple[str, str]] = []
    in_net = False
    for raw in text.splitlines():
        line = raw.strip()
        if line == "$net":
            in_net = True
            continue
        if in_net and line == "$endnet":
            in_net = False
            continue
        if in_net:
            values = line.split()
            if len(values) != 2:
                raise ConnectivityParseError(f"unrecognized globalmap entry: {line}")
            result.append((values[0], values[1]))
    if in_net:
        raise ConnectivityParseError("globalmap has no complete global net section")
    if "$net" not in text:
        raise ConnectivityParseError("globalmap has no net section")
    return tuple(sorted(result))


def parse_globalmap_models(text: str) -> tuple[tuple[str, str], ...]:
    """Parse OA view-to-generated-module bindings from ``globalmap``."""
    result: list[tuple[str, str]] = []
    in_model = False
    for raw in text.splitlines():
        line = raw.strip()
        if line == "$model":
            in_model = True
            continue
        if in_model and line == "$endmodel":
            in_model = False
            continue
        if in_model:
            values = line.split()
            if len(values) != 2:
                raise ConnectivityParseError(
                    f"unrecognized globalmap model entry: {line}"
                )
            result.append((values[0], values[1]))
    if in_model or "$model" not in text:
        raise ConnectivityParseError("globalmap has no complete model section")
    return tuple(sorted(result))


def parse_inherited_connections(
    text: str,
) -> tuple[tuple[str, str, str, str, str], ...]:
    """Parse SI's textual inherited-connection source inventory."""
    pattern = re.compile(
        r'^\s*"([^"]+)"\s+\("([^"]+)"\s+"([^"]+)"\s+"([^"]+)"\s+"([^"]+)"\)\s*$'
    )
    rows: list[tuple[str, str, str, str, str]] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        match = pattern.match(raw)
        if not match:
            raise ConnectivityParseError(
                f"unrecognized inherited connection entry: {raw.strip()}"
            )
        rows.append(tuple(match.groups()))
    return tuple(sorted(rows))
