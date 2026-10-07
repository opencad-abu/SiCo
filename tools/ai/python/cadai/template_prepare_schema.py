"""Common prepare envelope and exact-search options; no creation authorization."""

from __future__ import annotations

import copy

from .circuit_geometry_schema import GEOMETRY as GEOMETRY_INPUT
from .circuit_geometry_schema import LAYOUT
from .circuit_spec_schema import (
    BINDINGS,
    BOOL,
    SPEC,
    TEXT,
    CircuitSpecError,
    array,
    canonical,
    digest,
    enum,
    mapping,
    obj,
    validate,
)
from .template_adapt_schema import is_v2
from .template_circuit_schema import TEMPLATE_USE
from .template_reuse_schema import HEX, RULE_REF
from .template_schema import MAX_RESPONSE_BYTES, NAME, REF, TemplateError

VERSION = "cad.template.prepare-result.v1"
STATUSES = (
    "ready_for_creation_prepare",
    "needs_selection",
    "needs_mapping",
    "needs_adaptation",
    "needs_binding",
    "needs_geometry",
    "no_match",
    "inconclusive",
    "unavailable",
    "invalid_request",
)
TIERS = ("private", "project", "builtin")
POLICY = "quality_then_origin.v1"
COUNT = {"type": "integer", "minimum": 0, "maximum": 10000}
MILLISECONDS = {"type": "number", "minimum": 0, "maximum": 1e12}


class PrepareResponseBudget(TemplateError):
    """A bounded result cannot carry the complete artifacts; never truncate a ready plan."""


def nullable(schema):
    return {**schema, "type": [schema["type"], "null"]}


OPTIONS = obj(
    {
        "selection_mode": enum("best", "first_eligible"),
        "allowed_tiers": {**array(enum(*TIERS), 3, 1), "uniqueItems": True},
        "match_mode": enum("exact", "core"),
        "allowed_rule_refs": array(RULE_REF, 3),
        "return_alternatives": {"type": "integer", "minimum": 0, "maximum": 3},
        "max_query_time_ms": {"type": "integer", "minimum": 50, "maximum": 10000},
        "max_match_states": {"type": "integer", "minimum": 100, "maximum": 200000},
        "max_prepare_time_ms": {"type": "integer", "minimum": 100, "maximum": 60000},
        "ranking_policy": enum(POLICY),
    },
    (),
)
DEFAULTS = {
    "selection_mode": "best",
    "allowed_tiers": list(TIERS),
    "match_mode": "exact",
    "allowed_rule_refs": [],
    "return_alternatives": 2,
    "max_query_time_ms": 500,
    "max_match_states": 50000,
    "max_prepare_time_ms": 5000,
    "ranking_policy": POLICY,
}
ISSUE = obj({"code": NAME, "stage": NAME, "object_ref": nullable(NAME), "message": TEXT})
GRAPH_MAP = obj(
    {
        "devices": mapping(NAME, NAME, 64),
        "nets": mapping(NAME, NAME, 256),
        "ports": mapping(NAME, NAME, 128),
    }
)
TERMINAL_MAP = mapping(mapping(NAME, NAME, 64), NAME, 64)
CORE_MAPPING = obj(
    {
        "device_map": mapping(NAME, NAME, 64),
        "terminal_map": TERMINAL_MAP,
        "net_map": mapping(NAME, NAME, 256),
        "port_map": mapping(NAME, NAME, 128),
        "omitted_groups": {**array(NAME, 8), "uniqueItems": True},
    }
)
CANDIDATE = obj(
    {
        "template_ref": REF,
        "origins": {**array(enum(*TIERS), 3, 1), "uniqueItems": True},
        "eligibility": enum("eligible", "rejected", "needs_input", "inconclusive"),
        "hard_checks": array(NAME, 16),
        "rank_features": nullable(
            obj(
                {
                    "adaptation_cost": COUNT,
                    "target_coverage": {"type": "number", "minimum": 0, "maximum": 1},
                    "style_penalty": {"type": "number", "minimum": 0, "maximum": 0},
                    "origin_priority": {"type": "integer", "minimum": 0, "maximum": 2},
                    "mapping_digest": HEX,
                }
            )
        ),
        "mapping": nullable(GRAPH_MAP),
        "requirements": array(NAME, 32),
        # Core search retains the complete embedding so the read-only prepare
        # facade can pass the exact terminal/omission evidence to adapt().
        # Exact-search candidates omit these compatibility fields.
        "status": enum("matched", "needs_mapping", "needs_adaptation", "different",
                        "inconclusive", "invalid_request"),
        "reason": nullable(TEXT),
        "complete": BOOL,
        "states": COUNT,
        "mappings": array(CORE_MAPPING, 2),
        "adaptation_required": BOOL,
    }
, ("template_ref", "origins", "eligibility", "hard_checks", "rank_features",
   "mapping", "requirements"),
)
# Full existing preview/geometry outputs travel with the spec/use. P0 produces no plans.
PREVIEW = obj(
    {"preview_digest": HEX, "plan": obj({}, (), additionalProperties=True)},
    additionalProperties=True,
)
GEOMETRY = obj(
    {"geometry_plan_digest": HEX, "plan": obj({}, (), additionalProperties=True)},
    additionalProperties=True,
)
PLAN = obj(
    {
        "spec": SPEC,
        "template_use": TEMPLATE_USE,
        "preview": PREVIEW,
        "geometry": GEOMETRY,
        "geometry_input": GEOMETRY_INPUT,
    }
)
SEARCH = obj(
    {
        "complete": BOOL,
        "selection_mode": enum("best", "first_eligible"),
        "examined": COUNT,
        "remaining": COUNT,
        "stop_reason": NAME,
    }
)
DIAGNOSTICS = obj(
    {
        "elapsed_ms": MILLISECONDS,
        "budget_overrun_ms": MILLISECONDS,
        "states_used": {"type": "integer", "minimum": 0, "maximum": 210000},
        "unresolved": COUNT,
        "trace_omitted": COUNT,
        "cache": enum("disabled", "hit", "miss", "evicted", "fallback"),
        "plan_cache": enum("disabled", "hit", "miss", "evicted", "fallback"),
        "index": enum("disabled", "used", "index_fallback"),
        "versions": mapping(NAME, NAME, 16),
        "budget": obj(
            {
                "query_ms": MILLISECONDS,
                "match_states": {"type": "integer", "minimum": 0, "maximum": 200000},
            }
        ),
        "trace": array(obj({"template_ref": REF, "outcome": NAME, "reason": nullable(TEXT)}), 32),
        "placement": obj({"gap_count": COUNT, "omitted": COUNT, "gaps": array(ISSUE, 8)}),
        "relations": obj({
            "coverage": mapping(
                obj({"total": COUNT, "returned": COUNT, "omitted": COUNT}), NAME, 8),
            "gap_count": COUNT,
            "omitted": COUNT,
            "gaps": array(ISSUE, 8),
        }),
    }
, ("elapsed_ms", "budget_overrun_ms", "states_used", "unresolved", "trace_omitted",
   "cache", "index", "versions", "budget", "trace"),
)
RESULT = obj(
    {
        "schema": enum(VERSION),
        "status": enum(*STATUSES),
        "snapshot_ref": nullable(NAME),
        "search": SEARCH,
        "best_match": nullable(CANDIDATE),
        "alternatives": array(CANDIDATE, 3),
        "plan": nullable(PLAN),
        "issues": array(ISSUE, 64),
        "diagnostics": DIAGNOSTICS,
        # These additive aliases keep the first P2 core callers source
        # compatible while all new consumers use best_match/alternatives.
        "candidates": array(CANDIDATE, 10000),
        "states": COUNT,
        "reason": nullable(TEXT),
        "next_action": obj({
            "tool": enum("prepare_circuit_template_plan", "extract_circuit_templates"),
            "input_alternatives": array(array(enum(
                "binding_refs", "bindings", "geometry", "layout", "placement", "mapping",
                "template_ref", "spec", "allowed_rule_refs", "allowed_rules", "reuse", "options",
            ), 4, 1), 4, 1),
            "message": TEXT,
        }),
    }
, ("schema", "status", "snapshot_ref", "search", "best_match", "alternatives",
   "plan", "issues", "diagnostics"),
)


def search_options(values=None, template_ref=None):
    try:
        validate({} if values is None else values, OPTIONS)
        result = {**copy.deepcopy(DEFAULTS), **copy.deepcopy(values or {})}
        # The exact search entry point has its own API; this option is consumed
        # only by the P2 prepare facade.
        if template_ref is not None:
            validate(template_ref, REF, "template_ref")
        if (template_ref is not None or result["selection_mode"] == "first_eligible") and result[
            "return_alternatives"
        ]:
            raise CircuitSpecError("fixed reference/first_eligible requires return_alternatives=0")
        return result
    except CircuitSpecError as exc:
        raise TemplateError(str(exc)) from exc


def issue(code, message, object_ref=None, stage="search"):
    text = str(message).encode("utf-8", errors="replace").decode("utf-8")
    text = "".join(c if ord(c) >= 32 and ord(c) != 127 else " " for c in text)
    message = " ".join(text.split())[:512].rstrip() or code
    return {"code": code, "stage": stage, "object_ref": object_ref, "message": message}


def _plan_invariants(result):
    plan = result["plan"]
    if (result["status"] == "ready_for_creation_prepare") != (plan is not None):
        raise TemplateError("plan must exist exactly when ready_for_creation_prepare")
    if plan is None:
        return
    best = result["best_match"]
    if best is None or best["eligibility"] != "eligible" or best["requirements"]:
        raise TemplateError("ready requires a selected eligible candidate without missing inputs")
    preview, geometry = plan["preview"], plan["geometry"]
    try:
        validate(preview["plan"].get("bindings"), BINDINGS, "prepared bindings")
        validate(geometry["plan"].get("layout"), LAYOUT, "prepared layout")
    except CircuitSpecError as exc:
        raise TemplateError(str(exc)) from exc
    if preview["preview_digest"] != digest(preview["plan"]) or geometry[
        "geometry_plan_digest"
    ] != digest(geometry["plan"]):
        raise TemplateError("prepared artifact digest mismatch")
    if (
        preview["plan"].get("spec") != plan["spec"]
        or preview["plan"].get("template_use") != plan["template_use"]
        or plan["template_use"]["template_ref"] != best["template_ref"]
    ):
        raise TemplateError("prepared spec/use differs from selected candidate")
    use = plan["template_use"]
    if best["mapping"] != {
        "devices": use["device_map"] if is_v2(use) else
        {d["source_device"]: d["instance"] for d in use["devices"]},
        "nets": use["net_map"],
        "ports": use["port_map"],
    }:
        raise TemplateError("prepared mapping differs from selected candidate")
    # Read-only template preparation precedes native callback validation. Use
    # the structural preview's authoritative readiness rule, preserving its
    # unresolved callback evidence instead of pretending it was executed here.
    from .circuit_spec_plan import preview_circuit

    structural = preview_circuit(plan["spec"], preview["plan"]["bindings"])
    if (
        preview.get("geometry_preview_ready") is not True
        or structural["geometry_preview_ready"] is not True
        or preview["plan"].get("structural_valid") is not True
        or preview["plan"].get("binding_complete") != structural["plan"]["binding_complete"]
        or preview["plan"].get("issues") != structural["plan"]["issues"]
        or preview["plan"].get("binding_digest") != digest(preview["plan"]["bindings"])
        or geometry["plan"].get("preview_digest") != preview["preview_digest"]
        or geometry["plan"].get("template_use") != plan["template_use"]
        or geometry["plan"].get("geometry_checks_passed") is not True
        or geometry["plan"].get("issues") != []
        or geometry["plan"].get("geometry_digest") != digest(plan["geometry_input"])
        or plan["geometry_input"]["binding_digest"] != preview["plan"]["binding_digest"]
    ):
        raise TemplateError("ready requires complete structural and geometry artifacts")
    layout = geometry["plan"]["layout"]
    if (
        layout["grid"] != 0.0625
        or not layout.get("routing")
        or any(i["unconnected"] for i in plan["spec"]["instances"])
    ):
        raise TemplateError("ready plan is outside current creation capabilities")
    search = result["search"]
    if result["snapshot_ref"] is None:
        raise TemplateError("ready requires a fixed snapshot reference")
    if not search["complete"] and search["stop_reason"] != "first_eligible":
        raise TemplateError("ready requires a completed selection strategy")


def serialize_result(result):
    """Validate every branch and defensively copy it; this does not authorize OA writes."""
    try:
        encoded = canonical(result)
        if len(encoded.encode()) > MAX_RESPONSE_BYTES:
            raise PrepareResponseBudget("prepare result exceeds response budget")
        validate(result, RESULT, "prepare result")
    except CircuitSpecError as exc:
        raise TemplateError(str(exc)) from exc
    search = result["search"]
    if search["complete"] and (search["remaining"] or result["diagnostics"]["unresolved"]):
        raise TemplateError("complete search cannot contain remaining or unresolved candidates")
    if search["complete"] and result["snapshot_ref"] is None:
        raise TemplateError("complete search requires a fixed snapshot reference")
    if result["status"] == "inconclusive" and search["complete"]:
        raise TemplateError("inconclusive search cannot claim completion")
    if search["stop_reason"] == "first_eligible" and (
        search["selection_mode"] != "first_eligible" or search["complete"] or result["alternatives"]
    ):
        raise TemplateError("first_eligible requires an incomplete search without alternatives")
    if result["alternatives"] and result["best_match"] is None:
        raise TemplateError("alternatives require a best candidate")
    candidates = ([result["best_match"]] if result["best_match"] else []) + result["alternatives"]
    if len({c["template_ref"] for c in candidates}) != len(candidates):
        raise TemplateError("alternatives must exclude best and duplicate references")
    for candidate in candidates:
        eligible = candidate["eligibility"] == "eligible"
        if eligible != (candidate["rank_features"] is not None):
            raise TemplateError("only eligible candidates have rank features")
        if eligible and (not candidate["hard_checks"] or candidate["mapping"] is None):
            raise TemplateError("eligible candidates require hard checks and a mapping")
        if eligible and candidate["rank_features"]["mapping_digest"] != digest(
            candidate["mapping"]
        ):
            raise TemplateError("candidate mapping digest mismatch")
        if eligible:
            for values in candidate["mapping"].values():
                if len(set(values.values())) != len(values):
                    raise TemplateError("candidate mapping must be one-to-one")
    if result["status"] == "needs_mapping" and result["best_match"] is None:
        raise TemplateError("needs_mapping requires a candidate")
    if result["status"] == "no_match" and (not search["complete"] or candidates):
        raise TemplateError("no_match requires complete rejection of every candidate")
    _plan_invariants(result)
    return copy.deepcopy(result)
