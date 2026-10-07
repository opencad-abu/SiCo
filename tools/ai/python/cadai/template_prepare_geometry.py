"""Complete preparation geometry using the verified template use and existing planner."""

import copy

from .circuit_spec_schema import CircuitSpecError
from .template_adapt_schema import is_v2
from .template_prepare_feedback import relation_diagnostics, stage_issues
from .template_prepare_result import _with_search
from .template_prepare_schema import issue
from .template_relations import RelationEvidenceUnavailable
from .template_schema import TemplateError


def _layout(args, record, attached, use, check_budget):
    if "layout" in args:
        return args["layout"], None, None, None
    from .template_placement_layout import plan_layout
    from .template_relations import relations

    devices = use["device_map"] if is_v2(use) else {
        d["source_device"]: d["instance"] for d in use["devices"]}
    terminals = use["terminal_map"] if is_v2(use) else {
        d["source_device"]: d["terminal_map"] for d in use["devices"]}
    reference = relations(record)
    exceeded = check_budget()
    if exceeded is not None:
        return None, None, None, exceeded
    layout, report = plan_layout(
        attached["plan"]["spec"], attached["plan"]["bindings"], args["geometry"],
        reference, devices, args["placement"],
        port_map=use["port_map"], terminal_map=terminals,
    )
    from .template_prepare_feedback import placement_diagnostics

    return layout, placement_diagnostics(report), relation_diagnostics(reference), check_budget()


def prepare_geometry(args, result, best, record, attached, use, *, workspace, geometry_preview,
                     check_budget):
    """Produce a plan only after full geometry and current creator-capability checks."""
    best = copy.deepcopy(best)
    best["requirements"] = ["target_geometry"]

    def needs_geometry(rows):
        return _with_search(result, status="needs_geometry", reason="needs_geometry", best=best,
                            issue_rows=rows)

    missing = []
    if "geometry" not in args:
        missing.append("geometry (or retained binding_refs)")
    if "layout" not in args and "placement" not in args:
        missing.append("layout or placement with explicit routing")
    if missing:
        return needs_geometry([issue("needs_geometry", "Required: " + "; ".join(missing),
                                     best["template_ref"], stage="geometry")])
    try:
        layout, diagnostics, relation_evidence, exceeded = _layout(
            args, record, attached, use, check_budget
        )
        if exceeded is not None:
            return exceeded
        if diagnostics is not None:
            result = copy.deepcopy(result)
            result["diagnostics"]["placement"] = diagnostics
        if relation_evidence is not None:
            result = copy.deepcopy(result)
            result["diagnostics"]["relations"] = relation_evidence
        if layout["grid"] != 0.0625 or not layout.get("routing"):
            raise CircuitSpecError("creation requires grid=0.0625 and explicit routing.mode")
        geometry = geometry_preview(
            attached["plan"]["spec"], attached["plan"]["bindings"], attached["preview_digest"],
            args["geometry"], layout, template_use=use, workspace=workspace,
        )
    except RelationEvidenceUnavailable as exc:
        return needs_geometry([issue(exc.code, str(exc), best["template_ref"], stage="placement")])
    except (CircuitSpecError, TemplateError) as exc:
        return needs_geometry([issue("needs_geometry", str(exc), best["template_ref"],
                                     stage="geometry")])
    if not geometry["plan"]["geometry_checks_passed"]:
        return needs_geometry(stage_issues(geometry["plan"]["issues"], "geometry"))
    plan = {
        "spec": attached["plan"]["spec"],
        "template_use": use,
        "preview": attached,
        "geometry": geometry,
        "geometry_input": args["geometry"],
    }
    best["requirements"] = []
    return _with_search(result, status="ready_for_creation_prepare", reason="exhausted",
                        best=best, plan=plan, issue_rows=[])
