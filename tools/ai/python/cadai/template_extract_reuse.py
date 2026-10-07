"""Compile explicit extraction intent using the authoritative v3 builder."""

from .template_reuse_build import make_reusable
from .template_reuse_input import compile_contract, contract_issue
from .template_reuse_schema import ContractError
from .template_schema import CAPTURE_SCHEMA_V2, TemplateError
from .template_source_qualification import qualification_issues


def extract_reuse(base, options, capture_issues=()):
    """Return a qualified record or the unchanged reference with bounded source issues."""
    devices = base.get("topology", {}).get("devices", [])
    issues = list(capture_issues)
    if "schematic" not in base.get("assets", {}):
        issues.append(dict(code="missing_schematic", object_ref=None,
                           message="Recapture a readable schematic view."))
    elif base["source"].get("capture_schema") != CAPTURE_SCHEMA_V2:
        issues.append(dict(code="capture_v2_required", object_ref=None,
                           message="Reload the collector and recapture with capture v2."))
    elif not 1 <= len(devices) <= 64:
        issues.append(dict(code="device_count_unsupported", object_ref=None,
                           message="Reuse supports 1..64 electrical devices."))
    else:
        for device in devices:
            if device["role"] not in {"device", "unknown"}:
                issues.append(dict(code="unsupported_source_role", object_ref=device["source_name"],
                                   message="Reuse requires direct electrical devices.",
                                   master=device["master"], next_action="select_direct_source"))
            elif not device.get("classification_evidence"):
                issues.append(dict(code="classification_required", object_ref=device["source_name"],
                                   message="Supply reviewed classifications for this instance.",
                                   master=device["master"],
                                   terminals=[p["name"] for p in device["pins"]],
                                   required_fields=["kind", "attributes", "source_ref", "revision"],
                                   next_action="complete_saved_capture"))
        # Classification cannot resolve independent endpoint, net or source-proof defects.
        issues.extend(i for i in qualification_issues(base)
                      if i["code"] not in {"unsupported_device_class", "unknown_device_type"})
    contract = None
    if 1 <= len(devices) <= 64:
        try:
            contract = compile_contract(base, options)
        except ContractError as exc:
            issues.append(contract_issue(exc, base))
    if not issues:
        try:
            record = make_reusable(base, contract,
                                   {d["id"]: d["classification_evidence"] for d in devices})
        except ContractError as exc:
            issues.append(contract_issue(exc, base))
        except TemplateError as exc:
            issues.append(dict(code="source_not_qualified", object_ref=None,
                               message=str(exc)[:2000]))
        else:
            return record, dict(status="qualified", template_ref=record["template_ref"],
                                issues=[], issue_count=0)
    # The bounded response must expose a source repair blocker even when many
    # instances also need classification; otherwise the next action is misleading.
    issues.sort(key=lambda row: row["code"] == "classification_required")
    status = ("needs_classification" if all(i["code"] == "classification_required" for i in issues)
              else "needs_contract" if all(i.get("stage") == "contract" or
                                            i["code"] == "classification_required" for i in issues)
              else "unavailable")
    return base, dict(status=status, template_ref=None, issues=issues[:8], issue_count=len(issues))


# Deprecated compatibility name for callers pinned to the original whole-circuit API.
# Keep one implementation and remove this alias after downstream callers migrate.
whole_reuse = extract_reuse
