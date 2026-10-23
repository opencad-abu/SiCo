#!/usr/bin/env python3
"""Read-only conformance checks for a generated SystemVerilog real-number model.

Compares a candidate RNM module against the structural facts extracted from its
netlist (see parse_netlist.py) and, when asked, against a JSON block spec.  With
--compile the module is additionally syntax-checked by ``xrun -compile`` in a
scratch directory; the design under review is never modified.

Uses only the Python 3.9 standard library. Original helper imports remain
compatibility aliases to the responsibility owners; main and the standalone CLI
remain canonical. Retire aliases only after callers migrate at an API break.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from rnm_compile import CASCADED_AMS_ERROR_RE as CASCADED_AMS_ERROR_RE
from rnm_compile import ENVIRONMENT_NOISE_RE as ENVIRONMENT_NOISE_RE
from rnm_compile import compile_check as compile_check
from rnm_conformance import REAL_NET_TYPES as REAL_NET_TYPES
from rnm_conformance import check_modeling_style as check_modeling_style
from rnm_conformance import check_ports as check_ports
from rnm_conformance import check_spec as check_spec
from rnm_findings import SEVERITY_ORDER as SEVERITY_ORDER
from rnm_findings import Finding as Finding
from rnm_module import BLOCK_COMMENT_RE as BLOCK_COMMENT_RE
from rnm_module import DIRECTION_RE as DIRECTION_RE
from rnm_module import IDENT_RE as IDENT_RE
from rnm_module import LINE_COMMENT_RE as LINE_COMMENT_RE
from rnm_module import MODULE_RE as MODULE_RE
from rnm_module import NET_DECL_RE as NET_DECL_RE
from rnm_module import NET_TYPE_RE as NET_TYPE_RE
from rnm_module import PARAM_DECL_RE as PARAM_DECL_RE
from rnm_module import ModuleFacts as ModuleFacts
from rnm_module import declared_identifiers as declared_identifiers
from rnm_module import parse_module as parse_module
from rnm_module import strip_comments as strip_comments


def load_netlist_cell(path: Path, cell: Optional[str]) -> dict:
    payload = json.loads(path.read_text())
    cells = payload.get("cells") or []
    if not cells:
        raise ValueError(f"no cells in {path}")
    if cell:
        wanted = cell.casefold()
        for entry in cells:
            if str(entry.get("name", "")).casefold() == wanted:
                return entry
        raise ValueError(f"cell {cell!r} not present in {path}")
    if len(cells) > 1:
        names = ", ".join(str(entry.get("name")) for entry in cells)
        raise ValueError(f"{path} holds several cells ({names}); pass --cell")
    return cells[0]



def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check a generated SystemVerilog RNM against its netlist and spec.",
    )
    parser.add_argument("module", type=Path, help="candidate .sv file")
    parser.add_argument(
        "--netlist-json",
        type=Path,
        help="parse_netlist.py output used as the structural reference",
    )
    parser.add_argument("--cell", help="cell to select from the netlist JSON")
    parser.add_argument("--spec", type=Path, help="block spec (JSON)")
    parser.add_argument(
        "--compile",
        action="store_true",
        help="additionally syntax-check with xrun -compile in a scratch directory",
    )
    parser.add_argument(
        "--compile-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="extra argument passed to xrun (repeatable)",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--fail-on-warnings",
        action="store_true",
        help="exit non-zero for warnings as well as errors",
    )
    args = parser.parse_args(argv)

    try:
        text = args.module.read_text(errors="replace")
    except OSError as exc:
        print(f"cannot read {args.module}: {exc}", file=sys.stderr)
        return 2

    module = parse_module(text)
    if module is None:
        print(f"no module declaration found in {args.module}", file=sys.stderr)
        return 2

    findings: list[Finding] = []

    if args.netlist_json:
        try:
            cell = load_netlist_cell(args.netlist_json, args.cell)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"cannot use netlist JSON: {exc}", file=sys.stderr)
            return 2
        findings.extend(check_ports(module, list(cell.get("ports") or [])))

    spec: dict = {}
    if args.spec:
        try:
            spec = json.loads(args.spec.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"cannot read spec: {exc}", file=sys.stderr)
            return 2
        findings.extend(check_spec(module, spec))

    findings.extend(check_modeling_style(module, text))

    if args.compile:
        findings.extend(compile_check(args.module, args.compile_arg))

    findings.sort(key=lambda item: (SEVERITY_ORDER.get(item.severity, 9), item.category))

    errors = sum(1 for item in findings if item.severity == "error")
    warnings = sum(1 for item in findings if item.severity == "warning")

    if args.format == "json":
        print(
            json.dumps(
                {
                    "module": {
                        "path": str(args.module),
                        "name": module.name,
                        "ports": module.port_names(),
                        "directions": module.directions,
                        "net_types": module.net_types,
                        "parameters": module.parameters,
                    },
                    "counts": {
                        "error": errors,
                        "warning": warnings,
                        "info": len(findings) - errors - warnings,
                    },
                    "findings": [
                        {
                            "severity": item.severity,
                            "category": item.category,
                            "subject": item.subject,
                            "message": item.message,
                        }
                        for item in findings
                    ],
                },
                indent=2,
            )
        )
    else:
        print(f"module {module.name}  ({args.module})")
        ports = ", ".join(
            f"{name}:{module.directions.get(name, '?')}"
            f"/{module.net_types.get(name, '?')}"
            for name in module.port_names()
        )
        print(f"  ports: {ports or '-'}")
        if module.parameters:
            print(f"  parameters: {', '.join(module.parameters)}")
        print(f"  findings: {errors} error(s), {warnings} warning(s)")
        for item in findings:
            print(f"  [{item.severity}] {item.category}: {item.subject} - {item.message}")

    if errors:
        return 1
    if warnings and args.fail_on_warnings:
        return 1
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
