#!/usr/bin/env python3
"""Read-only structural extraction from Spectre or SPICE/DSPF netlists.

Emits a JSON circuit description (ports, devices, subcircuit instances, nets)
for use as the structural half of a schematic-to-RNM translation.  This script
never writes, rewrites, or executes a netlist and never starts a simulator.

Uses only the Python 3.9 standard library.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional


# ``subckt NAME a b c`` (Spectre) and ``.SUBCKT NAME a b c`` (SPICE/DSPF).
SPECTRE_SUBCKT_RE = re.compile(r"^\s*subckt\s+(\S+)\s*(.*)$", re.IGNORECASE)
SPICE_SUBCKT_RE = re.compile(r"^\s*\.subckt\s+(\S+)\s*(.*)$", re.IGNORECASE)
SPECTRE_ENDS_RE = re.compile(r"^\s*ends\b", re.IGNORECASE)
SPICE_ENDS_RE = re.compile(r"^\s*\.ends\b", re.IGNORECASE)

# Spectre element/instance: ``NAME (n1 n2 ...) master p=v ...``
SPECTRE_ELEMENT_RE = re.compile(r"^\s*(\S+)\s*\(([^)]*)\)\s+(\S+)\s*(.*)$")

PARAM_RE = re.compile(r"([A-Za-z_]\w*)\s*=\s*(\"[^\"]*\"|\([^)]*\)|\S+)")

# Directives that never describe connectivity.
SKIP_RE = re.compile(
    r"^\s*(?:\.?(?:include|lib|library|section|endlibrary|model|param|parameters|"
    r"paramset|global|option|options|simulator|temp|tran|dc|ac|noise|save|probe|"
    r"print|plot|end|ends|endsubckt|nodeset|ic|op|tf|pz|sens|four|meas|measure|"
    r"connect|autoground|title|subckt)\b|\*|//|;)",
    re.IGNORECASE,
)

# SPICE positional device shapes: prefix -> node count.
SPICE_NODE_COUNT = {
    "M": 4,  # MOSFET  d g s b
    "Q": 3,  # BJT     c b e   (4-terminal variants carry a substrate node)
    "J": 3,  # JFET
    "Z": 3,
    "R": 2,
    "C": 2,
    "L": 2,
    "D": 2,
    "V": 2,
    "I": 2,
    "E": 4,
    "G": 4,
    "F": 2,
    "H": 2,
    "S": 4,
    "W": 2,
    "T": 4,
}

MOS_MODEL_TOKENS = ("nch", "pch", "nmos", "pmos", "nfet", "pfet", "svt", "hvt", "lvt")


def classify_device(name: str, master: str) -> str:
    """Best-effort device class from the instance prefix and master name."""

    head = name[:1].upper()
    master_lower = master.lower()
    if head == "M" or any(token in master_lower for token in MOS_MODEL_TOKENS):
        return "mos"
    if head == "R":
        return "resistor"
    if head == "C":
        return "capacitor"
    if head == "L":
        return "inductor"
    if head == "D":
        return "diode"
    if head == "Q":
        return "bipolar"
    if head == "V":
        return "vsource"
    if head == "I":
        return "isource"
    if head == "X":
        return "subckt"
    return "unknown"


@dataclass(frozen=True)
class Device:
    name: str
    master: str
    kind: str
    nodes: tuple[str, ...]
    params: dict[str, str]
    line: int


@dataclass
class Cell:
    name: str
    ports: list[str] = field(default_factory=list)
    devices: list[Device] = field(default_factory=list)
    line: int = 0

    def internal_nets(self) -> list[str]:
        seen: dict[str, None] = {}
        ports = set(self.ports)
        for device in self.devices:
            for node in device.nodes:
                if node not in ports:
                    seen.setdefault(node, None)
        return sorted(seen)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for device in self.devices:
            counts[device.kind] = counts.get(device.kind, 0) + 1
        return dict(sorted(counts.items()))

    def submasters(self) -> list[str]:
        seen: dict[str, None] = {}
        for device in self.devices:
            if device.kind in {"subckt", "unknown"}:
                seen.setdefault(device.master, None)
        return sorted(seen)


def detect_dialect(text: str) -> str:
    """Classify the deck as ``spectre`` or ``spice`` from its subckt syntax."""

    if re.search(r"^\s*\.subckt\b", text, re.IGNORECASE | re.MULTILINE):
        return "spice"
    if re.search(r"^\s*subckt\b", text, re.IGNORECASE | re.MULTILINE):
        return "spectre"
    if re.search(r"^\s*simulator\s+lang\s*=\s*spectre", text, re.IGNORECASE | re.MULTILINE):
        return "spectre"
    return "spice"


def logical_lines(text: str, dialect: str) -> Iterable[tuple[int, str]]:
    """Yield (lineno, line) with continuations joined.

    Spectre continues a line with a trailing backslash; SPICE continues with a
    leading ``+`` on the following line.
    """

    raw_lines = text.splitlines()
    buffered = ""
    start = 0

    for index, raw in enumerate(raw_lines, start=1):
        line = raw.rstrip()

        if dialect == "spice":
            stripped = line.lstrip()
            if stripped.startswith("+"):
                if buffered:
                    buffered += " " + stripped[1:].strip()
                else:
                    # A continuation with nothing to continue; keep it visible.
                    buffered = stripped[1:].strip()
                    start = index
                continue
            if buffered:
                yield start, buffered
                buffered = ""
                start = 0
            if line.strip():
                buffered = line
                start = index
            continue

        # Spectre backslash continuation.
        if line.endswith("\\") and not line.endswith("\\\\"):
            if not buffered:
                start = index
            buffered += line[:-1].rstrip() + " "
            continue
        if buffered:
            yield start, buffered + line.lstrip()
            buffered = ""
            start = 0
            continue
        yield index, line

    if buffered:
        yield start or len(raw_lines), buffered.rstrip()


def strip_comment(line: str, dialect: str) -> str:
    text = line.split("//", 1)[0]
    if dialect == "spice":
        # ``$`` starts a trailing comment in most SPICE dialects.
        text = text.split("$", 1)[0]
    return text.rstrip()


def split_params(tail: str) -> tuple[list[str], dict[str, str]]:
    """Split a token tail into leading positional tokens and ``k=v`` pairs."""

    params: dict[str, str] = {}
    for match in PARAM_RE.finditer(tail):
        params[match.group(1).lower()] = match.group(2)

    first = PARAM_RE.search(tail)
    positional_text = tail[: first.start()] if first else tail
    positional = positional_text.split()
    return positional, params


def parse_spectre_element(name: str, node_text: str, master: str, tail: str, line: int) -> Device:
    nodes = tuple(node_text.split())
    _, params = split_params(tail)
    return Device(
        name=name,
        master=master,
        kind=classify_device(name, master),
        nodes=nodes,
        params=params,
        line=line,
    )


def parse_spice_element(line_text: str, line: int) -> Optional[Device]:
    """Parse a positional SPICE element line."""

    tokens_text = line_text.strip()
    if not tokens_text:
        return None

    positional, params = split_params(tokens_text)
    if not positional:
        return None

    name = positional[0]
    rest = positional[1:]
    head = name[:1].upper()

    if head == "X":
        # ``Xname n1 n2 ... master``: the master is the final positional token.
        if not rest:
            return None
        master = rest[-1]
        nodes = tuple(rest[:-1])
    else:
        count = SPICE_NODE_COUNT.get(head)
        if count is None:
            # Unknown prefix: assume every token but the last names a node.
            if len(rest) < 2:
                return None
            master = rest[-1]
            nodes = tuple(rest[:-1])
        else:
            nodes = tuple(rest[:count])
            remainder = rest[count:]
            master = remainder[0] if remainder else head
            if len(nodes) < count:
                # Truncated line; report what is present rather than guessing.
                master = master or head

    return Device(
        name=name,
        master=master,
        kind=classify_device(name, master),
        nodes=nodes,
        params=params,
        line=line,
    )


def parse_text(text: str, dialect: Optional[str] = None) -> tuple[str, list[Cell]]:
    resolved = dialect or detect_dialect(text)
    subckt_re = SPICE_SUBCKT_RE if resolved == "spice" else SPECTRE_SUBCKT_RE
    ends_re = SPICE_ENDS_RE if resolved == "spice" else SPECTRE_ENDS_RE

    cells: list[Cell] = []
    stack: list[Cell] = []

    for lineno, raw in logical_lines(text, resolved):
        line = strip_comment(raw, resolved)
        if not line.strip():
            continue

        subckt = subckt_re.match(line)
        if subckt:
            name = subckt.group(1)
            ports_text = subckt.group(2) or ""
            positional, _ = split_params(ports_text)
            cell = Cell(name=name, ports=positional, line=lineno)
            cells.append(cell)
            stack.append(cell)
            continue

        if ends_re.match(line):
            if stack:
                stack.pop()
            continue

        if SKIP_RE.match(line):
            continue

        if not stack:
            # Connectivity outside a subckt belongs to an implicit top level.
            continue

        if resolved == "spectre":
            element = SPECTRE_ELEMENT_RE.match(line)
            if not element:
                continue
            device = parse_spectre_element(
                element.group(1),
                element.group(2),
                element.group(3),
                element.group(4) or "",
                lineno,
            )
        else:
            device = parse_spice_element(line, lineno)

        if device is not None:
            stack[-1].devices.append(device)

    return resolved, cells


def cell_payload(cell: Cell, *, include_devices: bool) -> dict:
    payload: dict = {
        "name": cell.name,
        "line": cell.line,
        "ports": list(cell.ports),
        "port_count": len(cell.ports),
        "device_count": len(cell.devices),
        "device_summary": cell.summary(),
        "submasters": cell.submasters(),
        "internal_nets": cell.internal_nets(),
    }
    if include_devices:
        payload["devices"] = [
            {**asdict(device), "nodes": list(device.nodes)} for device in cell.devices
        ]
    return payload


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract structure from a Spectre or SPICE/DSPF netlist (read-only).",
    )
    parser.add_argument("path", type=Path, help="netlist file to read")
    parser.add_argument(
        "--dialect",
        choices=("auto", "spectre", "spice"),
        default="auto",
        help="netlist dialect; 'auto' infers it from the subckt syntax",
    )
    parser.add_argument(
        "--cell",
        action="append",
        default=[],
        metavar="NAME",
        help="restrict output to this cell (repeatable)",
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="output format",
    )
    parser.add_argument(
        "--devices",
        action="store_true",
        help="include the full per-device list (verbose for extracted decks)",
    )
    parser.add_argument(
        "--max-devices",
        type=int,
        default=200,
        help="omit the device list for cells above this size unless --devices is given",
    )
    args = parser.parse_args(argv)

    try:
        text = args.path.read_text(errors="replace")
    except OSError as exc:
        print(f"cannot read {args.path}: {exc}", file=sys.stderr)
        return 2

    dialect = None if args.dialect == "auto" else args.dialect
    resolved, cells = parse_text(text, dialect)

    if args.cell:
        wanted = {name.casefold() for name in args.cell}
        cells = [cell for cell in cells if cell.name.casefold() in wanted]
        if not cells:
            print(f"no matching cell in {args.path}", file=sys.stderr)
            return 1

    payload = {
        "source": {"path": str(args.path), "dialect": resolved},
        "cell_count": len(cells),
        "cells": [
            cell_payload(
                cell,
                include_devices=args.devices or len(cell.devices) <= args.max_devices,
            )
            for cell in cells
        ],
    }

    if args.format == "json":
        print(json.dumps(payload, indent=2))
        return 0

    print(f"source: {args.path}  dialect: {resolved}  cells: {len(cells)}")
    for cell in cells:
        print(f"\ncell {cell.name}  (line {cell.line})")
        print(f"  ports ({len(cell.ports)}): {' '.join(cell.ports) or '-'}")
        summary = ", ".join(f"{kind}={count}" for kind, count in cell.summary().items())
        print(f"  devices: {len(cell.devices)}  [{summary or 'none'}]")
        if cell.submasters():
            print(f"  submasters: {' '.join(cell.submasters())}")
        nets = cell.internal_nets()
        shown = " ".join(nets[:12])
        suffix = f" ... (+{len(nets) - 12})" if len(nets) > 12 else ""
        print(f"  internal nets ({len(nets)}): {shown or '-'}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
