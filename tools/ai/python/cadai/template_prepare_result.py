"""Build and finalize prepare responses against the request budget and catalog observation."""

import copy
import time

from .template_prepare_schema import (
    DEFAULTS,
    VERSION,
    PrepareResponseBudget,
    issue,
    serialize_result,
)
from .template_schema import TemplateUnavailable, digest


def _empty(status, reason, *, started, message, snapshot_ref=None):
    """Return a valid envelope before a target or catalog can be inspected."""
    elapsed = max(0.0, (time.monotonic() - started) * 1000)
    result = {
        "schema": VERSION,
        "status": status,
        "snapshot_ref": snapshot_ref,
        "search": {
            "complete": False,
            "selection_mode": "best",
            "examined": 0,
            "remaining": 0,
            "stop_reason": reason,
        },
        "best_match": None,
        "alternatives": [],
        "plan": None,
        "issues": [issue(reason, message)],
        "diagnostics": {
            "elapsed_ms": elapsed,
            "budget_overrun_ms": 0,
            "states_used": 0,
            "unresolved": 0,
            "trace_omitted": 0,
            "cache": "disabled",
            "index": "disabled",
            "versions": {"prepare": VERSION},
            "budget": {"query_ms": DEFAULTS["max_query_time_ms"],
                       "match_states": DEFAULTS["max_match_states"]},
            "trace": [],
        },
        "candidates": [],
        "states": 0,
        "reason": reason,
    }
    return serialize_result(result)



def _with_search(search, *, status=None, reason=None, best=None, plan=None, issue_rows=None):
    result = copy.deepcopy(search)
    if status is not None:
        result["status"] = status
    if reason is not None:
        result["reason"] = reason
        # Search termination remains evidence of the selection strategy;
        # downstream mapping/geometry reasons belong to the prepare stage only.
    if best is not None:
        result["best_match"] = best
        if "candidates" in result:
            result["candidates"] = [
                copy.deepcopy(best) if row["template_ref"] == best["template_ref"] else row
                for row in result["candidates"]
            ]
    if plan is not None:
        result["plan"] = plan
    if issue_rows is not None:
        result["issues"] = issue_rows
    return serialize_result(result)


def finish_result(result, catalog, started, options, *, clock):
    """All post-load return paths, including cache hits, share this final guard."""
    snapshot_ref = catalog._last_snapshot.snapshot_ref
    try:
        catalog._verify_snapshot()
    except (TemplateUnavailable, OSError) as exc:
        result = _empty("unavailable", "catalog_changed", started=started,
                        message=str(exc), snapshot_ref=snapshot_ref)
    elapsed = max(0.0, (clock() - started) * 1000)
    overrun = max(0.0, elapsed - options["max_prepare_time_ms"])
    if overrun and result["status"] not in {"unavailable", "invalid_request"}:
        result = _empty("inconclusive", "prepare_budget_exceeded", started=started,
                        message="template preparation exceeded max_prepare_time_ms",
                        snapshot_ref=snapshot_ref)
    result["snapshot_ref"] = snapshot_ref
    result["search"]["selection_mode"] = options["selection_mode"]
    result["diagnostics"].update(
        elapsed_ms=elapsed, budget_overrun_ms=overrun,
        budget={"query_ms": options["max_query_time_ms"],
                "match_states": options["max_match_states"]},
    )
    try:
        return serialize_result(result)
    except PrepareResponseBudget:
        # Final elapsed/budget diagnostics can cross the transport limit even
        # when the geometry-stage envelope just fit. Keep the snapshot verdict.
        failed = _empty("unavailable", "response_budget", started=started,
                        message="prepared artifacts exceed the response budget; no partial plan",
                        snapshot_ref=snapshot_ref)
        failed["search"]["selection_mode"] = options["selection_mode"]
        failed["diagnostics"].update(elapsed_ms=elapsed, budget_overrun_ms=overrun,
                                     budget=result["diagnostics"]["budget"])
        return serialize_result(failed)


def adapted_candidate(best, use, spec):
    """Promote only after adapt/attach have independently verified the complete use."""
    best = copy.deepcopy(best)
    best.update(eligibility="eligible", status="matched", reason=None,
                adaptation_required=False, requirements=["target_geometry"])
    best["hard_checks"] = list(dict.fromkeys([*best["hard_checks"], "verified_adaptation"]))
    best["rank_features"] = {
        "adaptation_cost": sum(a["rule_ref"]["id"] != "rename" for a in use["adaptations"]),
        "target_coverage": len(use["device_map"]) / len(spec["instances"]),
        "style_penalty": 0,
        "origin_priority": min(("private", "project", "builtin").index(t)
                               for t in best["origins"]),
        "mapping_digest": digest(best["mapping"]),
    }
    return best
