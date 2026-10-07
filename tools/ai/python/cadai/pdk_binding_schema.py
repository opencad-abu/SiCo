"""Explicit project choices for the session-PDK to static-circuit adapter."""

from .circuit_geometry_schema import DIRECTION
from .circuit_schema import tool
from .circuit_spec_schema import ID, MASTER, REF, SPEC, TEXT, array, enum, obj
from .pdk_extensions import EXTENSION_TOOL

BINDING_REFS = array(REF, 64, 1)
SELECTED_SPEC = {**SPEC, "required": [k for k in SPEC["required"]
                                    if k not in {"project_ref", "binding_snapshot"}]}


def binding_sources(schema, *, geometry=False):
    """Publish the same exclusive source choices expanded by PdkBindings."""
    explicit = ["bindings", "geometry"] if geometry else ["bindings"]
    schema["oneOf"] = [
        {"required": ["binding_refs"],
         "not": {"anyOf": [{"required": [field]} for field in explicit]}},
        {"required": explicit, "not": {"required": ["binding_refs"]}},
    ]
    return schema

WRITABLE = obj(
    {
        "name": ID,
        "policy": enum("static_true", "absent_condition", "project_extension", "cdf_callback"),
        "evidence": {**TEXT, "minLength": 1},
    }
)
PROPERTIES = {
    "snapshot_ref": REF,
    "device_ref": REF,
    "revision": {**REF, "description": "Use get_pdk_device.device.revision, "
                 "never search summary_revision"},
    "master_id": ID,
    "kind": enum("device", "design"),
    "project_ref": REF,
    "project_policy_ref": REF,
    "classification": MASTER["properties"]["classification"],
    "writable_parameters": array(WRITABLE, 256),
    "pin_escapes": array(obj({"figure_ref": REF, "escape": DIRECTION}), 512),
    # D5: CDF port -> model-deck terminal, confirmed by CAD; keyed by the CDF port name.
    "netlist_terminal_map": {"type": "object", "maxProperties": 64,
                             "propertyNames": ID, "additionalProperties": TEXT},
    "extension_ref": REF,
    "callback_order": array(ID, 16, 1),
    "response_mode": enum("full", "compact"),
    "use": enum("circuit", "testbench", "extraction", "verification"),
}
BINDING_TOOLS = [
    tool(
        "bind_pdk_device",
        "Adapt an explicitly selected search/get PDK snapshot/device/revision to generic master "
        "and geometry component rows. Reads every detail page and revalidates the live binding. "
        "With a workspace, confirmed standard usage, CDF rules and symbol geometry are mandatory; "
        "caller policies cannot grant permission. use defaults to circuit. Requires saved static flat symbols, scalar terminals and rectangular pin figures. "
        "Supply project policy/evidence for writable parameters and an explicit escape for EVERY "
        "pin figure. Use policy=cdf_callback for built-in parameter handling; GUI editable/display "
        "conditions are not evaluated or used as write gates. Returns conservative "
        "creator-checked body bounds and pin centers, excluding labels/pin markers. "
        "The netlist terminal order follows the model deck: when the collected deck terminals do "
        "not match the CDF port names by name, pass the CAD-confirmed netlist_terminal_map "
        "(CDF port -> deck terminal) instead of relying on port order. "
        "The backend uses the built-in CDF callback executor when no project override exists; "
        "omit extension_ref. For callbacks provide callback_order listing the writable parameters "
        "once each, in the intended edit order. Parameter values stay literal data. "
        "Use response_mode=compact and pass returned binding_ref in binding_refs to subsequent "
        "preview/prepare calls. Complete master and geometry remain on the server; never rebuild "
        "parameter metadata from a summary. Bound validation survives discovery snapshot eviction; "
        "equivalent selections in the same session keep their binding_ref. "
        "prepare and execute revalidate live dependencies. Geometry.instance "
        "and parameters_digest are placeholders for the caller's chosen instance/parameters. "
        "Optional classification supplies explicit project kind/attributes and source/revision "
        "evidence for template matching; a known collected classification must agree. "
        "Does not execute callbacks, select models, infer device classes or qualify simulation.",
        PROPERTIES,
        tuple(k for k in PROPERTIES
              if k not in {"extension_ref", "response_mode", "netlist_terminal_map",
                           "callback_order", "classification", "use"}),
    )
]
BINDING_TOOLS.append(EXTENSION_TOOL)
BINDING_NAMES = frozenset(t["name"] for t in BINDING_TOOLS)
