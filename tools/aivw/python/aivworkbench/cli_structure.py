"""Command adapter for the authoritative SI connectivity comparison."""

from __future__ import annotations

import argparse
from .cli_output import print_json


def check_connectivity(args: argparse.Namespace) -> int:
    from .connectivity import (
        Structure,
        compare_structures,
        parse_globalmap,
        parse_globalmap_models,
        parse_si_map,
        parse_si_netlists,
        parse_verilog,
    )
    from pathlib import Path

    try:
        aliases = parse_si_map(Path(args.map_path).read_text(encoding="utf-8"))
        globals_ = parse_globalmap(Path(args.globalmap).read_text(encoding="utf-8"))
        model_bindings = parse_globalmap_models(
            Path(args.globalmap).read_text(encoding="utf-8")
        )
        official = Structure(
            parse_si_netlists(
                Path(item).read_text(encoding="utf-8") for item in args.netlist
            ).modules,
            globals=globals_,
            aliases=aliases,
        )
        if not {module.name for module in official.modules}.issubset(
            {module for _view, module in model_bindings}
        ):
            raise ValueError("globalmap model bindings omit an SI module")
        candidate = Structure(
            parse_verilog(Path(args.candidate).read_text(encoding="utf-8")),
            globals=globals_,
        )
        findings = compare_structures(
            official,
            candidate,
            expected_module=args.module,
            expected_ports=tuple(args.port) or None,
            expected_globals=globals_,
        )
        payload = {
            "status": "PASS" if not findings else "FAIL_CONNECTIVITY",
            "findings": [item.as_dict() for item in findings],
        }
    except (OSError, ValueError) as exc:
        payload = {"status": "BLOCKED_INPUT", "error": str(exc)}
    if args.json:
        print_json(payload)
    else:
        print(f"status: {payload['status']}")
        if payload.get("findings"):
            print(f"finding_count: {len(payload['findings'])}")
    return 0 if payload["status"] == "PASS" else 1
