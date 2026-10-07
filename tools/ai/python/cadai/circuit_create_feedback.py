"""Translate retained native creation outcomes into actionable public feedback."""

import copy

_PREPARE_INPUTS = ["spec", "binding_refs", "preview_digest", "layout",
                   "geometry_plan_digest", "task_ref", "request_id"]
_RETAINED_FIELDS = ("request_id", "prepare_ref", "geometry_plan_digest", "circuit_ref",
                    "task_ref", "binding_refs", "target_ref", "target_action")
_ACTIONS = {
    "prepare_not_found": (
        "prepare_circuit_creation",
        "The retained preparation is unavailable or its digest expired; "
        "prepare the exact request again using the retained plan and binding inputs.",
        _PREPARE_INPUTS,
    ),
    "pdk_validation_required": (
        "prepare_circuit_creation",
        "Complete the current PDK/CDF validation for the retained request, "
        "through the existing preparation service before executing the same creation request.",
        _PREPARE_INPUTS,
    ),
    "creation_preflight_failed": (
        "prepare_circuit_creation",
        "Inspect the reported creation stage, target, binding, CDF, "
        "and geometry gaps before retrying with retained plan and binding inputs.",
        _PREPARE_INPUTS,
    ),
    "partial_circuit_retained": (
        "inspect_created_circuit",
        "A partial target was retained. Inspect it before any new write; "
        "do not automatically replay creation.",
        ["circuit_ref"],
    ),
    "created_circuit_changed": (
        "inspect_created_circuit",
        "The created target changed after preparation. "
        "Inspect the current target and prepare again before writing.",
        ["circuit_ref"],
    ),
}


def attach_creation_feedback(result, tool_name, args, *, ok):
    """Add one bounded next action while preserving all native status fields."""
    if not isinstance(result, dict):
        return result
    value = copy.deepcopy(result)
    code = value.get("code")
    if ok or value.get("next_action"):
        return value
    key = code
    if value.get("error") and value.get("circuit_ref"):
        key = "partial_circuit_retained"
    if key in _ACTIONS:
        action, message, inputs = _ACTIONS[key]
        fields = ("circuit_ref",) if action == "inspect_created_circuit" else _RETAINED_FIELDS
        retained = {k: copy.deepcopy(value.get(k) or args.get(k)) for k in fields
                    if value.get(k) or args.get(k)}
        if action == "prepare_circuit_creation":
            message += (" Reuse complete bindings/geometry if binding_refs were not used. "
                        "Keep request_id for identical retries; changed inputs require new "
                        "preparation and its own request_id.")
    else:
        action = tool_name
        retained = {k: copy.deepcopy(args[k]) for k in
                    _RETAINED_FIELDS if k in args}
        inputs = list(retained)
        message = (
            "Inspect the original session and exact target before further writes. "
            "If retrieving retained status, repeat only this identical request_id and inputs "
            "in the original session. A new request_id must not replay an unknown write. "
            "For execute_circuit_operation requests use get_circuit_operation with its original ID."
        )
    if value.get("stage"):
        message += " Failed stage: " + str(value["stage"])[:80] + "."
    value["next_action"] = {
        "tool": action,
        "input_fields": list(inputs),
        "retained_inputs": retained,
        "message": message[:1024],
        "automatic_retry": False,
    }
    return value


__all__ = ["attach_creation_feedback"]
