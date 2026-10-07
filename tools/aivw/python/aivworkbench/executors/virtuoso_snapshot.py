"""Authenticated Virtuoso snapshot collection and normalization owner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from ..executor import ExecutorContext, ExecutorResult
from ..profiles import product_root
from ..cdns_ipc import McpToolCall, CdnsIpcUnavailable
from ..workspace import sha256_file, stable_digest, write_json_once
from .virtuoso_support import _blocked, _call_snapshot_tools, _run_normalizer, _write_raw_payloads

_MAX_ITEMS = 100

def run_snapshot(context: ExecutorContext, *, call_tools=None) -> ExecutorResult:
    if context.metadata.get("read_only_structure_adapter") == "ldo.official_ams.v1":
        from .ldo_snapshot import run_ldo_snapshot
        return run_ldo_snapshot(context)
    target = context.recipe.target
    views = target["source_views"]
    schematic_view = views.get("schematic")
    symbol_view = views.get("symbol")
    if not isinstance(schematic_view, str) or not isinstance(symbol_view, str):
        return _blocked(
            context,
            "BLOCKED_INPUT",
            "recipe must select schematic and symbol source views",
        )
    library = str(target["library"])
    cell = str(target["cell"])
    calls = (
        McpToolCall("get_context", {}),
        McpToolCall("inspect_library", {"name": library}),
        McpToolCall(
            "inspect_schematic",
            {
                "lib": library,
                "cell": cell,
                "view": schematic_view,
                "view_type": "schematic",
                "include_placement": False,
                "max_items": _MAX_ITEMS,
            },
        ),
        McpToolCall(
            "inspect_symbol_ports",
            {
                "lib": library,
                "cell": cell,
                "view": symbol_view,
                "view_type": "schematicSymbol",
                "max_items": _MAX_ITEMS,
            },
        ),
        McpToolCall(
            "inspect_schematic",
            {
                "lib": library,
                "cell": cell,
                "view": schematic_view,
                "view_type": "schematic",
                "include_placement": False,
                "max_items": _MAX_ITEMS,
            },
        ),
        McpToolCall(
            "inspect_symbol_ports",
            {
                "lib": library,
                "cell": cell,
                "view": symbol_view,
                "view_type": "schematicSymbol",
                "max_items": _MAX_ITEMS,
            },
        ),
        McpToolCall("inspect_library", {"name": library}),
        McpToolCall("get_context", {}),
    )
    entry = product_root().parent / "ai" / "python" / "sico-ai"
    try:
        results = (call_tools or _call_snapshot_tools)(
            calls,
            environment=context.environment,
            cad_ai_entry=entry,
            timeout=context.timeout,
        )
    except CdnsIpcUnavailable as exc:
        status = (
            "BLOCKED_INPUT"
            if exc.code
            in {
                "cellview_open_failed",
                "library_not_found",
                "no_current_cellview",
                "result_too_large",
            }
            else "BLOCKED_ENVIRONMENT"
        )
        return _blocked(context, status, str(exc), code=exc.code)
    payloads = [dict(item.payload) for item in results]
    raw_artifacts = _write_raw_payloads(context, calls, payloads)
    context_before, library_before, schematic_before, symbol_before = payloads[:4]
    schematic_after, symbol_after, library_after, context_after = payloads[4:]
    for label, payload in (
        ("schematic_before", schematic_before),
        ("symbol_before", symbol_before),
        ("schematic_after", schematic_after),
        ("symbol_after", symbol_after),
    ):
        if payload.get("truncated") is not False:
            return _blocked(
                context,
                "BLOCKED_INPUT",
                f"{label} did not explicitly prove truncated=false",
                code="truncated_inspection",
                artifacts=raw_artifacts,
            )
        if payload.get("modified") is not False:
            return _blocked(
                context,
                "BLOCKED_UNSAVED_SOURCE",
                f"{label} did not prove a saved cellview (modified=false)",
                code="unsaved_or_unknown_source",
                artifacts=raw_artifacts,
            )
    expected = {
        "schematic": {"lib": library, "cell": cell, "view": schematic_view},
        "symbol": {"lib": library, "cell": cell, "view": symbol_view},
    }
    for kind, first, second in (
        ("schematic", schematic_before, schematic_after),
        ("symbol", symbol_before, symbol_after),
    ):
        if first.get("cellview") != expected[kind] or second.get("cellview") != expected[kind]:
            return _blocked(
                context,
                "BLOCKED_INPUT",
                f"{kind} inspection identity does not match the recipe target",
                code="identity_mismatch",
                artifacts=raw_artifacts,
            )
        if stable_digest(first) != stable_digest(second):
            return _blocked(
                context,
                "STALE_SOURCE",
                f"{kind} changed while the snapshot was being collected",
                code="source_changed",
                artifacts=raw_artifacts,
            )
    context_identity = context_before.get("edit_cellview")
    if not isinstance(context_identity, Mapping):
        context_identity = context_before.get("display_cellview")
    if not isinstance(context_identity, Mapping):
        return _blocked(
            context,
            "BLOCKED_INPUT",
            "Virtuoso context did not expose an active cellview identity",
            code="active_view_identity_unavailable",
            artifacts=raw_artifacts,
        )
    if (
        context_identity.get("lib") != library
        or context_identity.get("cell") != cell
        or context_identity.get("view") != schematic_view
    ):
        return _blocked(
            context,
            "BLOCKED_INPUT",
            "active Virtuoso cellview identity does not match the recipe target",
            code="active_view_identity_mismatch",
            artifacts=raw_artifacts,
        )
    context_after_identity = context_after.get("edit_cellview")
    if not isinstance(context_after_identity, Mapping):
        context_after_identity = context_after.get("display_cellview")
    if not isinstance(context_after_identity, Mapping):
        return _blocked(
            context,
            "BLOCKED_INPUT",
            "Virtuoso context did not expose an active cellview identity after snapshot",
            code="active_view_identity_unavailable",
            artifacts=raw_artifacts,
        )
    if dict(context_after_identity) != dict(context_identity):
        return _blocked(
            context,
            "STALE_SOURCE",
            "active Virtuoso cellview changed while the snapshot was being collected",
            code="active_view_changed",
            artifacts=raw_artifacts,
        )
    if stable_digest(library_before) != stable_digest(library_after):
        return _blocked(
            context,
            "STALE_SOURCE",
            "library mapping changed while the snapshot was being collected",
            code="library_mapping_changed",
            artifacts=raw_artifacts,
        )
    library_path_text = library_before.get("path")
    if not isinstance(library_path_text, str) or not library_path_text:
        return _blocked(
            context,
            "BLOCKED_INPUT",
            "Virtuoso session did not return a readable target library path",
            code="library_path_unavailable",
            artifacts=raw_artifacts,
        )
    library_path = Path(library_path_text).expanduser().resolve(strict=False)
    if "smic28" in str(library_path).casefold():
        return _blocked(
            context,
            "BLOCKED_INPUT",
            "snapshot target resolves into the excluded smic28 workspace",
            code="forbidden_workspace",
            artifacts=raw_artifacts,
        )
    raw_root = context.gate_root / "raw"
    schematic_path = raw_root / "schematic-before.json"
    symbol_path = raw_root / "symbol-before.json"
    normalized_path = context.gate_root / "normalized-netlist.json"
    input_manifest_path = context.gate_root / "input-manifest.json"
    normalize_log = context.gate_root / "normalize.log"
    normalization = _run_normalizer(
        schematic_path,
        symbol_path,
        normalized_path,
        input_manifest_path,
        normalize_log,
        context,
    )
    normalizer_artifacts = (normalize_log,)
    if normalization["returncode"] != 0 or normalization["timed_out"]:
        for path in (normalized_path, input_manifest_path):
            if path.is_file():
                normalizer_artifacts += (path,)
        return _blocked(
            context,
            (
                "BLOCKED_ENVIRONMENT"
                if normalization.get("status") == "BLOCKED_ENVIRONMENT"
                else "BLOCKED_INPUT" if not normalization["timed_out"] else "BLOCKED_TIMEOUT"
            ),
            "inspection normalization did not establish a complete structure snapshot",
            code=str(normalization.get("code", "normalization_failed")),
            artifacts=(*raw_artifacts, *normalizer_artifacts),
            extra={"normalizer": normalization},
        )
    try:
        normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _blocked(
            context,
            "BLOCKED_INPUT",
            f"normalizer produced unreadable output: {exc}",
            code="invalid_normalized_output",
            artifacts=(*raw_artifacts, normalize_log),
        )
    if normalized.get("status") != "PASS" or normalized.get("cell_count") != 1:
        return _blocked(
            context,
            "BLOCKED_INPUT",
            "normalizer did not return one complete PASS cell",
            code="invalid_normalized_output",
            artifacts=(*raw_artifacts, normalize_log, normalized_path, input_manifest_path),
        )
    source_generation = stable_digest(
        {
            "target": expected,
            "library": library_before,
            "schematic": schematic_before,
            "symbol": symbol_before,
        }
    )
    evidence_path = context.gate_root / "snapshot-evidence.json"
    write_json_once(
        evidence_path,
        {
            "schema_version": 1,
            "status": "PASS",
            "target": {"library": library, "cell": cell, "views": expected},
            "library_path": str(library_path),
            "source_generation": source_generation,
            "formal_saved_state": True,
            "double_read_equal": True,
            "max_items": _MAX_ITEMS,
            "context_before": context_before,
            "context_after": context_after,
            "normalizer": normalization,
            "input_manifest_sha256": sha256_file(input_manifest_path),
            "authority": "authenticated-cdns-ipc-read-only",
            "structure_authority": "preliminary_snapshot_not_cadence_si",
        },
    )
    return ExecutorResult(
        "PASS",
        {
            "source_generation": source_generation,
            "normalized_sha256": sha256_file(normalized_path),
            "target": {"library": library, "cell": cell, "module": str(target["module"])},
            "view_identity": {
                "library": library,
                "cell": cell,
                "view": schematic_view,
                "view_type": "schematic",
                "kind": "schematic",
            },
            "formal_saved_state": True,
            "double_read_equal": True,
            "library_path": str(library_path),
            "normalizer": normalization,
        },
        {
            "snapshot_evidence": evidence_path.relative_to(context.run.payload_root).as_posix(),
            "normalized_structure": normalized_path.relative_to(
                context.run.payload_root
            ).as_posix(),
            "input_manifest": input_manifest_path.relative_to(
                context.run.payload_root
            ).as_posix(),
            "source_generation": source_generation,
            "normalized_sha256": sha256_file(normalized_path),
            # Snapshot binding is a cross-component contract.  Keep the
            # authoritative identity in ``outputs`` because recipe manifests
            # and the cad/ai controller consume that namespace.  The summary
            # copy above remains for compatibility with older reports.
            "target": {
                "library": library,
                "cell": cell,
                "module": str(target["module"]),
            },
            "view_identity": {
                "library": library,
                "cell": cell,
                "view": schematic_view,
                "view_type": "schematic",
                "kind": "schematic",
            },
            "structure_authoritative": False,
        },
        (
            *raw_artifacts,
            normalize_log,
            normalized_path,
            input_manifest_path,
            evidence_path,
        ),
    )
