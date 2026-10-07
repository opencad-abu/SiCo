"""Read-only orchestration for exact/core template preparation.

This module owns the public prepare facade only.  Matching, adaptation,
preview and geometry verification remain in their existing modules; the facade
selects one versioned path and returns the common prepare envelope.
"""

from __future__ import annotations

import copy
import time

from .circuit_geometry import preview_geometry
from .circuit_geometry_schema import GEOMETRY, LAYOUT
from .circuit_schema import tool
from .circuit_spec_plan import preview_circuit
from .circuit_spec_schema import BINDINGS, CircuitSpecError, array, validate
from .pdk_binding_schema import BINDING_REFS, SELECTED_SPEC
from .pdk_schema import PdkUnavailable
from .template_adapt_schema import MAP
from .template_catalog import TemplateCatalog
from .template_placement_schema import OPTIONS as PLACEMENT_OPTIONS
from .template_prepare_catalog import catalog_records
from .template_prepare_feedback import add_next_action, stage_issues
from .template_prepare_geometry import prepare_geometry
from .template_prepare_plan_cache import read_ready, ready_key, store_ready
from .template_prepare_result import _empty, _with_search, adapted_candidate, finish_result
from .template_prepare_schema import (
    GRAPH_MAP,
    OPTIONS,
    PrepareResponseBudget,
    issue,
    search_options,
)
from .template_prepare_search import search_snapshot
from .template_reuse_schema import RULE_REF
from .template_rules import RULE_DIGESTS, rule_ref
from .template_schema import REF, SCHEMA_V3, TemplateError, TemplateUnavailable
from .template_target import target_topology

TOOL_NAME = "prepare_circuit_template_plan"
_SPEC = {**SELECTED_SPEC, "required": list(SELECTED_SPEC["required"])}
_MAPPING = {"oneOf": [MAP, GRAPH_MAP]}
_RULES = array(RULE_REF, 3)

TOOLS = [
    tool(
        TOOL_NAME,
        "Prepare a read-only template plan using one fixed catalog snapshot. "
        "Dispatches exact or v3 core matching, reuses the existing adaptation and "
        "preview/geometry validators, and never writes OA. A complete response is "
        "still only a creation preparation; live binding and creation remain separate. "
        "Use binding_refs to reuse retained device metadata, and placement with explicit "
        "routing to derive layout from the verified mapping in this call. Supply either "
        "placement or layout. Explicit allowed_rules names (rename, omit_optional_group, "
        "add_boundary_group) resolve installed identities; alternatively use allowed_rule_refs. "
        "Never supply both. Template permission is independently checked. "
        "Missing inputs are reported in next_action.",
        {
            "spec": _SPEC,
            "bindings": BINDINGS,
            "binding_refs": BINDING_REFS,
            "geometry": GEOMETRY,
            "layout": LAYOUT,
            "placement": {**PLACEMENT_OPTIONS, "required": ["routing"]},
            "template_ref": REF,
            "mapping": _MAPPING,
            "allowed_rule_refs": _RULES,
            "allowed_rules": {**array({"type": "string", "enum": list(RULE_DIGESTS)}, 3),
                              "uniqueItems": True},
            "options": OPTIONS,
        },
        ("spec",),
    )
]
TOOLS[0]["inputSchema"]["not"] = {"anyOf": [
    {"required": ["binding_refs", "bindings"]},
    {"required": ["binding_refs", "geometry"]},
    {"required": ["placement", "layout"]},
    {"required": ["allowed_rules", "allowed_rule_refs"]},
]}
TOOL_NAMES = frozenset(entry["name"] for entry in TOOLS)


def _legacy_use(record, target, spec, graph):
    """Convert an exact whole-graph mapping to the existing v1 use contract."""
    devices = {row["id"]: row for row in target["devices"]}
    instances = {row["id"]: row for row in spec["instances"]}
    source_devices = {row["id"]: row for row in record["topology"]["devices"]}
    rows = []
    for source_id, instance_id in sorted(graph["devices"].items()):
        source = source_devices[source_id]
        target_device = devices[instance_id]
        instance = instances[instance_id]
        terminal_map = {pin["name"]: pin["name"] for pin in source["pins"]}
        if {pin["name"] for pin in source["pins"]} != {
            pin["name"] for pin in target_device["pins"]
        }:
            raise TemplateError("exact mapping terminal inventory differs")
        rows.append({"source_device": source_id, "instance": instance_id,
                     "master": instance["master"], "terminal_map": terminal_map})
    return {
        "schema": "cad.circuit.template-use.v1",
        "template_ref": record["template_ref"],
        "devices": rows,
        "net_map": dict(graph["nets"]),
        "port_map": dict(graph["ports"]),
    }


def prepare_plan(args, workspace=None, pdk_bindings=None):
    """Prepare one pure plan; all downstream artifacts are independently rechecked."""
    return add_next_action(_prepare_plan(args, workspace, pdk_bindings), args)


def _prepare_plan(args, workspace, pdk_bindings):
    started = time.monotonic()
    try:
        validate(args, TOOLS[0]["inputSchema"], "prepare_circuit_template_plan")
    except CircuitSpecError as exc:
        return _empty("invalid_request", "invalid_request", started=started, message=str(exc))
    if "allowed_rules" in args:
        args = {k: v for k, v in args.items() if k != "allowed_rules"} | dict(
            allowed_rule_refs=[rule_ref(n) for n in args["allowed_rules"]])
    try:
        options = search_options(args.get("options"), args.get("template_ref"))
    except TemplateError as exc:
        return _empty("invalid_request", "invalid_request", started=started, message=str(exc))
    deadline = started + options["max_prepare_time_ms"] / 1000
    catalog_snapshot_ref = None

    def budget_result():
        if time.monotonic() > deadline:
            return _empty(
                "inconclusive",
                "prepare_budget_exceeded",
                started=started,
                message="template preparation exceeded max_prepare_time_ms",
                snapshot_ref=catalog_snapshot_ref,
            )
        return None
    if "binding_refs" in args:
        try:
            if pdk_bindings is None:
                raise CircuitSpecError("Selected device data expired; bind selected devices again")
            args = pdk_bindings.resolve_request(args, include_geometry=True)
        except (CircuitSpecError, PdkUnavailable) as exc:
            return _empty("needs_binding", "binding_resolution_failed", started=started,
                          message=str(exc))
    spec = copy.deepcopy(args["spec"])
    bindings = args.get("bindings")
    if not isinstance(bindings, dict):
        return _empty("needs_binding", "needs_binding", started=started,
                      message="target bindings are required before template matching")
    try:
        preview = preview_circuit(spec, bindings)
        if not preview["geometry_preview_ready"]:
            result = _empty("needs_binding", "needs_binding", started=started,
                            message="resolve target binding metadata before template matching")
            return _with_search(
                result, issue_rows=stage_issues(preview["plan"]["issues"], "binding"))
        target = target_topology(preview["plan"]["spec"], preview["plan"]["bindings"])
    except CircuitSpecError as exc:
        text = str(exc)
        status = "needs_binding" if text.startswith("needs_binding") else "invalid_request"
        return _empty(status, status, started=started, message=text)

    catalog = TemplateCatalog(workspace=workspace)
    try:
        records, origins = catalog_records(catalog, set(options["allowed_tiers"]))
    except TemplateUnavailable as exc:
        return _empty(
            "unavailable",
            "catalog_unavailable",
            started=started,
            message=str(exc),
            snapshot_ref=getattr(getattr(catalog, "_last_snapshot", None), "snapshot_ref", None),
        )
    catalog_snapshot_ref = getattr(
        getattr(catalog, "_last_snapshot", None), "snapshot_ref", None
    )
    key = None
    try:
        result = budget_result()
        if result is None and catalog._cache.enabled:
            key = ready_key(args, preview, target, options, records, origins, catalog_snapshot_ref)
            result = read_ready(catalog._cache, key, records, workspace, preview_geometry,
                                budget_result)
        if result is None:
            result = _prepare_snapshot(
                args, preview, target, options, catalog, records, origins,
                catalog_snapshot_ref, workspace, budget_result, started,
            )
    except (TemplateUnavailable, OSError) as exc:
        result = _empty("unavailable", "catalog_changed", started=started,
                        message=str(exc), snapshot_ref=catalog_snapshot_ref)
    except PrepareResponseBudget:
        result = _empty("unavailable", "response_budget", started=started,
                        message="prepared artifacts exceed the response budget; no partial plan",
                        snapshot_ref=catalog_snapshot_ref)
    result = finish_result(result, catalog, started, options, clock=time.monotonic)
    if key and result["diagnostics"].get("plan_cache") != "hit":
        result = store_ready(catalog._cache, key, result)
    return result


def _prepare_snapshot(args, preview, target, options, catalog, records, origins,
                      catalog_snapshot_ref, workspace, budget_result, started):
    selected_ref = args.get("template_ref")
    exceeded = budget_result()
    if exceeded is not None:
        return exceeded
    if args.get("template_ref") is not None and args["template_ref"] not in records:
        return _empty(
            "unavailable",
            "reference_unavailable",
            started=started,
            message="fixed template reference is not available for preparation",
            snapshot_ref=catalog_snapshot_ref,
        )
    if selected_ref:
        records = {selected_ref: records[selected_ref]}
    mode = options["match_mode"]
    index_status = "disabled"
    if not selected_ref:
        indexed, index_status = catalog.index_candidates(records, target, mode)
        # Preserve no-match/unavailable semantics for an empty indexed pool.
        # Nonempty subsets were validated against this fixed snapshot.
        if indexed:
            records = indexed
    graph_map = args.get("mapping") or {}
    exact_mapping = "devices" in graph_map
    if mode == "core" and not any(r.get("schema_version") == SCHEMA_V3 for r in records.values()):
        return _empty("unavailable", "no_catalog_data", started=started,
                      message="no qualified v3 core templates are available")
    result = search_snapshot(
        args, preview["plan"], target, options, records, origins,
        snapshot_ref=catalog_snapshot_ref, index_status=index_status, cache=catalog._cache,
    )
    exceeded = budget_result()
    if exceeded is not None:
        return exceeded
    if result["status"] in {"invalid_request", "unavailable", "no_match", "inconclusive"}:
        return result
    if result["status"] == "needs_mapping":
        # Core already consumed explicit mappings; never pick its first ambiguous
        # embedding. Exact needs the caller's complete, verified graph mapping.
        if mode == "core":
            return result
        if not exact_mapping or graph_map != result["best_match"]["mapping"]:
            return result
    if result["status"] == "needs_selection" and not selected_ref:
        return result
    best = result.get("best_match")
    if best is None:
        return result
    if not selected_ref and result["alternatives"] and (
        best["eligibility"] == "needs_input"
        or any(row["eligibility"] == "needs_input" for row in result["alternatives"])
    ):
        return _with_search(result, status="needs_selection", reason="selection_required")
    record = records[best["template_ref"]]
    try:
        if mode == "core":
            mappings = best.get("mappings") or []
            if not mappings:
                if result["status"] == "needs_adaptation":
                    return _with_search(result, issue_rows=[issue(
                        best.get("reason") or "adaptation_required",
                        "Source contract/target adaptation requires review: "
                        + (best.get("reason") or "adaptation_required"), best["template_ref"],
                        stage="adaptation")])
                return _with_search(
                    result,
                    status="needs_mapping",
                    reason="mapping_confirmation",
                    issue_rows=[issue("mapping_confirmation", "core mapping is required")],
                )
            mapping = copy.deepcopy(mappings[0])
            if graph_map.get("device_map") and graph_map["device_map"] != mapping["device_map"]:
                raise TemplateError("explicit device_map differs from the verified core mapping")
            from .template_adaptation import adapt

            use = adapt(record, preview["plan"]["spec"], preview["plan"]["bindings"], mapping,
                        args.get("allowed_rule_refs", []))
        else:
            use = _legacy_use(record, target, preview["plan"]["spec"], best["mapping"])
        from .template_circuit import attach_template

        attached = attach_template(preview, use, workspace=workspace, record=record)
        if mode == "core" and best["eligibility"] == "needs_input":
            best = adapted_candidate(best, use, attached["plan"]["spec"])
    except (CircuitSpecError, TemplateError, KeyError, ValueError) as exc:
        text = str(exc)
        if "mapping" in text or "terminal" in text:
            return _with_search(
                result,
                status="needs_mapping",
                reason="mapping_confirmation",
                issue_rows=[issue("mapping_confirmation", text, best["template_ref"])],
            )
        return _with_search(
            result,
            status="needs_adaptation",
            reason="adaptation_required",
            issue_rows=[
                issue("adaptation_required", text, best["template_ref"], stage="adaptation")
            ],
        )
    exceeded = budget_result()
    if exceeded is not None:
        return exceeded
    return prepare_geometry(args, result, best, record, attached, use,
                            workspace=workspace, geometry_preview=preview_geometry,
                            check_budget=budget_result)


def call_prepare(name, args, workspace=None, pdk_bindings=None):
    if name != TOOL_NAME:
        raise TemplateError("unknown template prepare tool: " + name)
    return prepare_plan(args, workspace=workspace, pdk_bindings=pdk_bindings)
