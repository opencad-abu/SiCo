"""Read installed device support without loading code or executing CDF callbacks."""

import json

from .circuit_spec_schema import REF, TARGET, validate
from .circuit_schema import tool
from .skill_result import call_skill
from .pdk_schema import PdkUnavailable


class DeviceSupportUnavailable(PdkUnavailable):
    def __init__(self, code, target, diagnostic):
        name = "/".join(target[k] for k in ("library", "cell", "view"))
        message = (
            "Device support for automatic creation is not ready for " + name + ". PDK "
            "metadata may still be available; the agent must check installed device support "
            "or recommend a supported alternative for confirmation. "
            "No circuit has been created; no internal identifier is needed from the user."
        )
        super().__init__(code, message)
        capability = {
            "metadata_ready": None,
            "vendor_callbacks_present": None,
            "copilot_extension_registered": False,
            "builtin_cdf_executor_available": False,
            "creator_validated": None,
            "automatic_creation_supported": None,
        }
        if isinstance(diagnostic, dict):
            rows = diagnostic.get("items")
            if isinstance(rows, list):
                valid = [row[0] for row in rows
                         if isinstance(row, list) and len(row) == 3 and row[2] is True]
                capability["copilot_extension_registered"] = any(
                    ref != "builtin:cdf-callbacks:v1" for ref in valid)
                capability["builtin_cdf_executor_available"] = "builtin:cdf-callbacks:v1" in valid
            capability["vendor_callbacks_present"] = diagnostic.get("vendor_callbacks_present")
            diagnostic = dict(diagnostic, capability=capability)
        self.detail = dict(ok=False, code=code, message=message, target=target,
                           user_input_required=False, diagnostic=diagnostic,
                           capability=capability,
                           next_action="inspect_cdf_and_callback_diagnostics")


EXTENSION_TOOL = tool(
    "list_project_extensions",
    "Read installed support for one exact selected device and resolved library path. "
    "For agent diagnostics only; never ask the user for registration identifiers. "
    "Does not load code, run CDF callbacks or validate requested parameters. "
    "bind_pdk_device performs this lookup automatically when device support is required.",
    {"target": TARGET, "library_path": {**REF, "maxLength": 2048, "pattern": r"^/.*"}},
    ("target", "library_path"),
)


def discover_extensions(client, device):
    target, path = device["target"], device["library"]["resolved_path"]
    values = [target[k] for k in ("library", "cell", "view")] + [path]
    if client is None:
        raise DeviceSupportUnavailable("device_support_discovery_unavailable", target,
                                       "native client unavailable")
    code = "aiCrListExtensions(" + " ".join(json.dumps(v) for v in values) + ")"
    ok, result = call_skill(client, code)
    if not ok or not isinstance(result, dict) or result.get("ok") is not True:
        raise DeviceSupportUnavailable("device_support_discovery_unavailable", target, result)
    rows = result.get("items")
    if rows is None and "items" in result:
        rows = []
    if not isinstance(rows, list) or len(rows) > 128 or any(
        not isinstance(row, list) or len(row) != 3
        or any(not isinstance(v, str) or not v or len(v) > 256 for v in row[:2])
        or type(row[2]) is not bool for row in rows
    ) or len({row[0] for row in rows}) != len(rows):
        raise DeviceSupportUnavailable("device_support_discovery_unavailable", target,
                                       "invalid native support registration evidence")
    return {**result, "items": rows}


def select_extension(result, target, requested=None):
    rows = result["items"]
    valid = [row[0] for row in rows if row[2] is True]
    if requested is not None:
        if requested in valid:
            return requested
        code = "device_support_registration_invalid"
    elif len(valid) == 1:
        return valid[0]
    else:
        code = "device_support_ambiguous" if len(valid) > 1 else "device_support_missing"
    raise DeviceSupportUnavailable(code, target, result)


def call_extensions(args, client):
    validate(args, EXTENSION_TOOL["inputSchema"])
    return discover_extensions(client, dict(target=args["target"],
                                           library=dict(resolved_path=args["library_path"])))
