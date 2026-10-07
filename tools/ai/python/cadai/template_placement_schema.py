"""Shared options for template-derived placement, independent of tool orchestration."""

from .circuit_geometry_schema import GEOMETRY, LAYOUT
from .circuit_schema import tool
from .circuit_spec_schema import BINDINGS, ID, NET_NAME, obj
from .pdk_binding_schema import BINDING_REFS, SELECTED_SPEC
from .template_adapt_schema import MAP
from .template_circuit_schema import TEMPLATE_USE
from .template_schema import REF

POSITIVE = {"type": "number", "minimum": 0.000001, "maximum": 1000}
OPTIONS = obj(
    {
        "grid": {"type": "number", "minimum": 0.000001, "maximum": 1},
        "clearance": {"type": "number", "minimum": 0, "maximum": 10},
        "spacing": {"type": "string", "enum": ["loose", "template", "compact"]},
        "stub_length": POSITIVE,
        "row_gap": POSITIVE,
        "column_gap": POSITIVE,
        "port_gap": POSITIVE,
        "port_pitch": POSITIVE,
        "port_roles": {
            "type": "object",
            "maxProperties": 128,
            "additionalProperties": {"type": "string", "enum": ["power", "ground", "signal"]},
        },
        "routing": LAYOUT["properties"]["routing"],
    },
    (),
)


TOOL_NAME = "preview_template_placement"
MAX_RESPONSE_BYTES = 900000

DEVICE_MAP = {
    "type": "object",
    "maxProperties": 64,
    "propertyNames": {"type": "string", "pattern": r"^d[0-9]+$"},
    "additionalProperties": ID,
}

TOOLS = [
    tool(
        TOOL_NAME,
        "Compute a deterministic target layout from one stored template's relative placement "
        "relations plus the selected target master geometry: shared rows, columns and evidenced "
        "terminal axes keep their alignment and order across rows, "
        "left-right mirrored pairs keep their mirrored orientation. Ports keep template "
        "sides and relative positions; missing positions use the default bands "
        "(power, bidirectional, input, ground; output right). Port connections default "
        "to labelled stubs independently of internal routing. Spacing is "
        "recomputed from target body and annotation bounds on the target grid. Source pitch "
        "is a lower bound; missing terminal evidence is reported. Returns layout, a "
        "relations report and explicit gaps; pass the layout to preview_circuit_geometry / "
        "prepare_circuit_creation unchanged. Read-only; no OA writes.",
        {
            "template_ref": REF,
            "spec": SELECTED_SPEC,
            "bindings": BINDINGS,
            "binding_refs": BINDING_REFS,
            "geometry": GEOMETRY,
            "template_use": TEMPLATE_USE,
            "device_map": DEVICE_MAP,
            "terminal_map": MAP["properties"]["terminal_map"],
            "port_map": {"type": "object", "maxProperties": 128,
                         "propertyNames": NET_NAME, "additionalProperties": NET_NAME},
            "options": OPTIONS,
        },
        ("template_ref", "spec"),
    )
]
TOOL_NAMES = frozenset(entry["name"] for entry in TOOLS)
