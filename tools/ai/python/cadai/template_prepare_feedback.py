"""Bounded prepare-stage failures and request-specific follow-up inputs."""

from .circuit_spec_schema import canonical
from .template_prepare_schema import PrepareResponseBudget, issue, serialize_result


def stage_issues(rows, stage):
    """Keep authoritative codes and object details within the common envelope budget."""
    result = []
    limit = 63 if len(rows) > 64 else 64
    for row in rows[:limit]:
        detail = {k: v for k, v in row.items() if k != "code"}
        result.append(issue(row["code"], canonical(detail) if detail else row["code"],
                            row.get("instance") or row.get("port") or row.get("device"),
                            stage=stage))
    if len(rows) > limit:
        result.append(issue("additional_issues_omitted", str(len(rows) - limit)
                            + " additional issues omitted", stage=stage))
    return result


def placement_diagnostics(report):
    """Expose drawing fallbacks independently of blocking geometry checks."""
    rows = list(report.get("gaps", []))
    rows.extend(dict(code=r["reason"], port=r["port"], fallback=r["source"])
                for r in report.get("port_rows", []) if r.get("reason"))
    return dict(gap_count=len(rows), omitted=max(0, len(rows) - 8),
                gaps=stage_issues(rows[:8], "placement"))


def relation_diagnostics(reference):
    """Expose bounded source relation coverage without assigning electrical meaning."""
    rows = list(reference.get("gaps", []))
    coverage = reference.get("coverage", {})
    return {
        "coverage": {name: {
            "total": int(value.get("total", 0)),
            "returned": int(value.get("returned", 0)),
            "omitted": int(value.get("omitted", 0)),
        } for name, value in list(coverage.items())[:8]},
        "gap_count": len(rows),
        "omitted": max(0, len(rows) - 8),
        "gaps": stage_issues(rows[:8], "relations"),
    }


def add_next_action(result, args):
    """Attach guidance at the facade boundary, never to cached search responses."""
    tool = "prepare_circuit_template_plan"
    status = result["status"]
    if status == "inconclusive":
        search = result.get("search", {})
        diagnostics = result.get("diagnostics", {})
        alternatives = [["template_ref"], ["options"]]
        message = (
            f"The result is inconclusive, not no_match: examined {search.get('examined', 0)} "
            f"candidates, {search.get('remaining', 0)} remain, "
            f"{diagnostics.get('unresolved', 0)} unresolved, "
            f"stop_reason={search.get('stop_reason', 'unknown')}. "
            "Submit a fixed template_ref to inspect one "
            "candidate, or resubmit bounded options (max_query_time_ms/max_match_states) "
            "for a fresh bounded search; no candidate is selected automatically."
        )
        if search.get("stop_reason") == "prepare_budget_exceeded":
            message = ("Preparation exceeded max_prepare_time_ms. Search/geometry completion "
                       "is unknown; resubmit options.max_prepare_time_ms within its bound or "
                       "a fixed template_ref. Do not treat this result as no_match.")
    elif status == "needs_binding":
        alternatives = [["binding_refs"], ["bindings"]]
        message = ("Use bind_pdk_device to bind or correct selected devices, then resubmit "
                   "binding_refs (or complete bindings). Resolve the reported classification, "
                   "terminal, parameter or callback issues; keep the target spec explicit.")
        for row in result["issues"]:
            row["stage"] = "binding"
    elif status == "needs_geometry":
        geometry = [] if "geometry" in args or "binding_refs" in args else ["geometry"]
        if "layout" not in args and "placement" not in args:
            alternatives = [geometry + ["placement"], geometry + ["layout"]]
        elif geometry:
            alternatives = [geometry]
        else:
            alternatives = [["binding_refs", "placement" if "placement" in args else "layout"]
                            if "binding_refs" in args else
                            ["geometry", "placement" if "placement" in args else "layout"]]
        message = ("Supply the missing inputs or correct the reported geometry issues. "
                   "placement derives layout from the verified mapping; layout supplies explicit "
                   "positions and anchor choices. Both require explicit routing.mode and the "
                   "current creation grid 0.0625. Retained binding_refs supply geometry.")
    elif status == "needs_mapping":
        alternatives = [["mapping"]]
        message = ("Confirm a complete mapping for best_match using its returned mapping/mappings "
                   "as candidates. Preserve terminal polarity; resubmit mapping explicitly. "
                   "An ambiguous candidate is not an authorized selection.")
    elif status == "needs_selection":
        alternatives = [["template_ref"]]
        message = ("Select a template_ref from the candidates and resubmit with "
                   "options.return_alternatives=0. Selection does not authorize adaptation.")
    elif status == "needs_adaptation" and (result.get("best_match") or {}).get("reason") in {
        "omission_rule_not_allowed", "boundary_rule_not_allowed", "rename_rule_not_allowed",
    }:
        tool = "extract_circuit_templates"
        alternatives = [["template_ref", "reuse"]]
        message = ("Source contract permission is missing: " + result["best_match"]["reason"]
                   + ". With the workspace retained capture, use best_match.template_ref and "
                   "explicit reviewed reuse intent to create a new private reference offline, "
                   "then prepare it. Otherwise recapture the explicit source. The old record "
                   "stays pinned; target allowed_rules cannot extend source permissions.")
    elif status == "needs_adaptation":
        alternatives = [["spec", "allowed_rules"], ["spec", "allowed_rule_refs"]]
        message = ("Resolve the reported target-spec or adaptation-rule requirements. Keep all "
                   "target connections and parameters explicit; grant only intended rules in "
                   "allowed_rules (rename, omit_optional_group, add_boundary_group), or existing "
                   "allowed_rule_refs, never both. Source template permission is also required. "
                   "The preparer does not grant permissions automatically.")
    else:
        return result
    result["next_action"] = {
        "tool": tool, "input_alternatives": alternatives,
        "message": message,
    }
    try:
        return serialize_result(result)
    except PrepareResponseBudget:
        # Guidance is additive; an already validated nonready response must not
        # become a transport exception just because it cannot fit this hint.
        result.pop("next_action")
        return serialize_result(result)
