"""Ready-plan reuse keyed by resolved inputs, immutable records and planner versions."""

import copy

from .circuit_geometry_schema import PLAN_VERSION
from .circuit_spec_schema import CircuitSpecError
from .template_adapt_schema import VERIFIER_VERSION
from .template_cache import plan_cache_key
from .template_core_match import VERSION as EMBEDDING_VERSION
from .template_placement_layout import PLACEMENT_SCHEMA
from .template_prepare_schema import PrepareResponseBudget, serialize_result
from .template_prepare_search import _search_dependencies
from .template_relations import RELATIONS_SCHEMA
from .template_rules import RULE_DIGESTS
from .template_schema import TemplateError

# Increment when planning semantics change without a public schema change.
PLANNER_VERSION = "20260922.prepare-plan.v1"


def ready_key(args, preview, target, options, records, origins, snapshot_ref):
    dependencies = _search_dependencies(
        args, preview["plan"]["spec"], preview["plan"]["bindings"], target, options,
        records, origins, snapshot_ref, options["match_mode"], "snapshot", args.get("mapping"),
    )
    dependencies.update(
        request=args, geometry=PLAN_VERSION, placement=PLACEMENT_SCHEMA,
        relations=RELATIONS_SCHEMA, adaptation=VERIFIER_VERSION,
        embedding=EMBEDDING_VERSION, rules=RULE_DIGESTS, planner=PLANNER_VERSION,
    )
    return plan_cache_key(dependencies)


def read_ready(cache, key, records, workspace, geometry_preview, check_budget):
    """Hit validation reuses the electrical and geometry authorities, never receipts."""
    cached = cache.get("plan", key)
    if cached is None:
        return None
    try:
        cached = serialize_result(cached)
        if cached["status"] != "ready_for_creation_prepare":
            return None
        plan = cached["plan"]
        record = records[plan["template_use"]["template_ref"]]
        from .circuit_spec_plan import preview_circuit
        from .template_circuit import attach_template

        attached = attach_template(
            preview_circuit(plan["spec"], plan["preview"]["plan"]["bindings"]),
            plan["template_use"], workspace=workspace, record=record,
        )
        if attached != plan["preview"]:
            return None
        exceeded = check_budget()
        if exceeded is not None:
            return exceeded
        geometry = geometry_preview(
            plan["spec"], attached["plan"]["bindings"], attached["preview_digest"],
            plan["geometry_input"], plan["geometry"]["plan"]["layout"],
            template_use=plan["template_use"], workspace=workspace,
        )
        if geometry != plan["geometry"] or not geometry["plan"]["geometry_checks_passed"]:
            return None
        cached["diagnostics"].update(cache="hit", elapsed_ms=0, budget_overrun_ms=0)
        cached["diagnostics"]["plan_cache"] = "hit"
        return serialize_result(cached)
    except (TemplateError, CircuitSpecError, KeyError, TypeError, ValueError):
        # A corrupt/obsolete pure value falls back to the normal planner.
        return None


def store_ready(cache, key, result):
    if result["status"] != "ready_for_creation_prepare":
        return result
    stored = copy.deepcopy(result)
    stored["diagnostics"].update(cache="miss", plan_cache="miss")
    try:
        stored = serialize_result(stored)
    except PrepareResponseBudget:
        return result
    event = cache.put("plan", key, stored)
    annotated = copy.deepcopy(result)
    annotated["diagnostics"].update(cache=event, plan_cache=event)
    try:
        return serialize_result(annotated)
    except PrepareResponseBudget:
        # Optional cache telemetry must not invalidate the finalized plan.
        return result
