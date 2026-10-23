"""Hash inspected inputs and describe the provenance of a normalized manifest."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from rnm_inspection import SCHEMA_VERSION, TOOL_VERSION, BlockedInput


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BlockedInput(f"cannot hash {path}: {exc}", code="unreadable_input")
    return "sha256:" + digest.hexdigest()


def build_manifest(
    normalized: Mapping[str, Any],
    *,
    schematic_path: Path,
    symbol_path: Path,
    output_path: Path | None,
    manifest_path: Path | None,
) -> dict[str, Any]:
    cell = normalized["cells"][0]
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "status": normalized.get("status", "PASS"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "host": platform.node(),
        "python": sys.version.split()[0],
        "inputs": {
            "schematic_json": {"path": str(schematic_path.resolve()), "sha256": sha256_file(schematic_path)},
            "symbol_json": {"path": str(symbol_path.resolve()), "sha256": sha256_file(symbol_path)},
        },
        "outputs": {
            "normalized_netlist": str(output_path.resolve()) if output_path else None,
            "manifest": str(manifest_path.resolve()) if manifest_path else None,
        },
        "source_hash": normalized["source"]["source_hash"],
        "cell": {
            "library": cell["provenance"]["identity"]["lib"],
            "name": cell["name"],
            "schematic_view": cell["provenance"]["identity"]["view"],
            "port_count": cell["port_count"],
            "instance_count": len(cell["instances"]),
            "net_count": len(cell["nets"]),
            "connection_count": len(cell["connections"]),
            "port_order_source": cell["provenance"]["port_order_source"],
        },
        "truth": {
            "structure": "mcp.inspect_schematic with complete instance terminal-to-net data",
            "interface": "mcp.inspect_symbol_ports cross-checked against schematic terminals",
            "name_map": "identity only; official si map/globalmap still required for M0 completion",
        },
    }
