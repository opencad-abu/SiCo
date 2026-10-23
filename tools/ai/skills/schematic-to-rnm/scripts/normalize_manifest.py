#!/usr/bin/env python3
"""Normalize bounded inspections through the existing script and Python entry.

Private development tooling only; never part of a runtime release payload.
Compatibility imports retain the original error, hash and helper identities.
Retire those imports only after supported script users migrate to their owners;
normalize and the CLI remain the canonical orchestration entry.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from rnm_connectivity import _is_schematic_pin_marker as _is_schematic_pin_marker
from rnm_connectivity import _normalize_schematic as _normalize_schematic
from rnm_inspection import REQUIRED_SCHEMATIC_KEYS as REQUIRED_SCHEMATIC_KEYS
from rnm_inspection import REQUIRED_SYMBOL_KEYS as REQUIRED_SYMBOL_KEYS
from rnm_inspection import SCHEMA_VERSION as SCHEMA_VERSION
from rnm_inspection import TOOL_VERSION as TOOL_VERSION
from rnm_inspection import BlockedInput as BlockedInput
from rnm_inspection import _as_dicts as _as_dicts
from rnm_inspection import _cellview as _cellview
from rnm_inspection import _integer as _integer
from rnm_inspection import _read_json as _read_json
from rnm_inspection import _require_ok as _require_ok
from rnm_inspection import _text as _text
from rnm_inspection import _unwrap_mcp as _unwrap_mcp
from rnm_provenance import _canonical_json as _canonical_json
from rnm_provenance import build_manifest as build_manifest
from rnm_provenance import sha256_file as sha256_file
from rnm_provenance import sha256_json as sha256_json
from rnm_symbol import _normalize_symbol as _normalize_symbol
from rnm_symbol import _ordered_symbol_terminals as _ordered_symbol_terminals


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()


def normalize(
    schematic: Mapping[str, Any],
    symbol: Mapping[str, Any],
    *,
    schematic_path: Path,
    symbol_path: Path,
) -> dict[str, Any]:
    symbol_ports, symbol_meta = _normalize_symbol(symbol, symbol_path)
    _require_ok(schematic, "schematic", REQUIRED_SCHEMATIC_KEYS, schematic_path)
    schematic_terminal_entries = _as_dicts(schematic["terminals"], "terminals", str(schematic_path))
    schematic_names = {
        _text(item.get("name"), "terminal.name", str(schematic_path), required=True).casefold()
        for item in schematic_terminal_entries
    }
    symbol_names = {str(item["name"]).casefold() for item in symbol_ports}
    if schematic_names != symbol_names:
        raise BlockedInput(
            "schematic terminal set/order does not match symbol port order",
            code="order_mismatch",
        )
    cell = _normalize_schematic(schematic, schematic_path, symbol_ports)
    schematic_identity = _cellview(schematic, str(schematic_path))
    symbol_identity = symbol_meta["identity"]
    if schematic_identity["lib"] != symbol_identity["lib"] or schematic_identity["cell"] != symbol_identity["cell"]:
        raise BlockedInput(
            "schematic and symbol cellview identities do not match",
            code="identity_mismatch",
        )
    cell["ports"] = [str(item["name"]) for item in symbol_ports]
    cell["port_count"] = len(cell["ports"])
    cell["provenance"]["port_order_source"] = symbol_meta["order_source"]
    source = {
        "kind": "virtuoso-mcp",
        "schematic": str(schematic_path.resolve()),
        "symbol": str(symbol_path.resolve()),
        "dialect": "virtuoso",
        "source_hash": sha256_json({"schematic": schematic, "symbol": symbol}),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "status": "PASS",
        "source": source,
        "cell_count": 1,
        "cells": [cell],
    }


def _write_json(path: Path, value: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot write {path}: {exc}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Normalize complete MCP schematic/symbol inspection JSON into an M0 netlist manifest."
    )
    parser.add_argument("--schematic-json", required=True, type=Path)
    parser.add_argument("--symbol-json", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="write normalized netlist JSON")
    parser.add_argument("--manifest-output", type=Path, help="write input manifest JSON")
    args = parser.parse_args(argv)
    if args.output is None and args.manifest_output is None:
        parser.error("at least one of --output or --manifest-output is required")
    if args.output is not None and args.manifest_output is not None and _same_path(
        args.output, args.manifest_output
    ):
        parser.error("--output and --manifest-output must use different paths")
    for output_path in (args.output, args.manifest_output):
        if output_path is not None and (
            _same_path(output_path, args.schematic_json)
            or _same_path(output_path, args.symbol_json)
        ):
            parser.error("output paths must not overwrite either inspection input")

    try:
        schematic = _read_json(args.schematic_json)
        symbol = _read_json(args.symbol_json)
        normalized = normalize(
            schematic,
            symbol,
            schematic_path=args.schematic_json,
            symbol_path=args.symbol_json,
        )
    except BlockedInput as exc:
        blocked = {
            "schema_version": SCHEMA_VERSION,
            "tool_version": TOOL_VERSION,
            "status": "BLOCKED_INPUT",
            "error": {"code": exc.code, "message": str(exc)},
        }
        if args.output:
            _write_json(args.output, blocked)
        if args.manifest_output:
            _write_json(
                args.manifest_output,
                {
                    **blocked,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "inputs": {
                        "schematic_json": str(args.schematic_json.resolve()),
                        "symbol_json": str(args.symbol_json.resolve()),
                    },
                },
            )
        print(f"BLOCKED_INPUT: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.output:
        _write_json(args.output, normalized)
    if args.manifest_output:
        _write_json(
            args.manifest_output,
            build_manifest(
                normalized,
                schematic_path=args.schematic_json,
                symbol_path=args.symbol_json,
                output_path=args.output,
                manifest_path=args.manifest_output,
            ),
        )
    print(
        f"PASS: normalized {normalized['cells'][0]['name']} "
        f"({normalized['cells'][0]['port_count']} ports, "
        f"{len(normalized['cells'][0]['instances'])} instances, "
        f"{len(normalized['cells'][0]['nets'])} nets)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
