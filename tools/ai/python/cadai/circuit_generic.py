"""MCP-facing generic circuit/TB preview; live PDK discovery is an external tool."""

from __future__ import annotations

import json

from .circuit_geometry import preview_geometry
from .circuit_geometry_schema import GEOMETRY, LAYOUT
from .circuit_schema import tool
from .circuit_spec_plan import preview_circuit
from .circuit_spec_schema import BINDINGS, REF, CircuitSpecError, validate
from .pdk_binding_schema import BINDING_REFS, SELECTED_SPEC, binding_sources
from .template_circuit_schema import TEMPLATE_USE

GENERIC_NAMES = frozenset({"preview_circuit_spec", "preview_circuit_geometry"})
GENERIC_TOOLS = [
    tool(
        "preview_circuit_spec",
        "Validate a circuit or testbench specification against a project PDK binding snapshot "
        "and return a read-only topology/layout plan. Prefer binding_refs returned by "
        "bind_pdk_device; the server supplies full metadata, project identity and geometry. "
        "Never reconstruct masters. A testbench must instantiate a design symbol as DUT; "
        "create and verify a missing symbol before planning, never copy or flatten the "
        "design schematic into the TB. "
        "With explicit bindings, the binding snapshot must have been obtained "
        "from the current Virtuoso session by the project-context tool. This call does not "
        "discover or execute PDK callbacks, open/create OA views, or claim live geometry "
        "or simulation readiness. Optional template_use rechecks every source endpoint and "
        "adds relative placement/provenance to the preview digest.",
        {"spec": SELECTED_SPEC, "bindings": BINDINGS, "binding_refs": BINDING_REFS,
         "template_use": TEMPLATE_USE},
        ("spec",),
    ),
    tool(
        "preview_circuit_geometry",
        "Transform explicit instance/port placements and selected master-local pin anchors into "
        "grid-aligned short labelled stubs. Consumes a prior preview_digest and project geometry "
        "snapshot, or binding_refs to reuse server-retained geometry; does not write OA. "
        "Without layout.routing the preview uses labelled stubs. "
        "{mode: end_to_end, engine} routes internal device terminals. External ports "
        "keep labelled stubs by default; only ports=route includes them in continuous "
        "routing. mode=stub keeps all stubs. The default engine "
        "cadence_route publishes deterministic "
        "connection edges; creation resolves every real pin centre in the live cellview and "
        "lets the Virtuoso router draw the path, falling back to a labelled stub per endpoint "
        "when a route is refused. engine=planner returns the exact planned orthogonal "
        "coordinates in the preview (traceable offline) and reports a reason per fallback. "
        "Follow geometry_preview_ready from the spec preview, not creation_ready. "
        "Ports sit on the boundary and every port escape must point from the pin into the "
        "circuit (left ports escape right, right ports escape left): a stub that points away "
        "is legal but lands in layout_review as port_stub_points_outward. "
        "Template port sides and positions take priority over default bands; only "
        "missing positions use power/signal/ground ordering. Terminal-row alignment "
        "applies only to continuously routed ports without template positions. "
        "Checks occupied bounds, foreign-net pin/stub contacts and TB/pin "
        "placement rules. Port escape and planned pin orientation stay separate: the escape "
        "draws the wire only, while the orientation follows the direction and facing edge. "
        "Pass template_use again for a template-backed preview_digest. "
        "Does not certify label rendering or current session bindings.",
        {
            "spec": SELECTED_SPEC,
            "bindings": BINDINGS,
            "binding_refs": BINDING_REFS,
            "preview_digest": REF,
            "geometry": GEOMETRY,
            "layout": LAYOUT,
            "template_use": TEMPLATE_USE,
        },
        ("spec", "preview_digest", "layout"),
    ),
]

for _definition in GENERIC_TOOLS:
    binding_sources(_definition["inputSchema"],
                    geometry=_definition["name"] == "preview_circuit_geometry")


def checked_preview(args, workspace=None):
    required = {"spec", "bindings"}
    if (
        not isinstance(args, dict)
        or not required <= set(args)
        or set(args) - required - {"template_use"}
    ):
        raise CircuitSpecError(
            "preview_circuit_spec requires spec, bindings and optional template_use"
        )
    result = preview_circuit(args["spec"], args["bindings"])
    if "template_use" in args:
        from .template_circuit import attach_template

        result = attach_template(result, args["template_use"], workspace=workspace)
    return _bounded(result)


def call_generic(name, args, workspace=None, pdk_bindings=None):
    validate(args, next(t["inputSchema"] for t in GENERIC_TOOLS if t["name"] == name))
    if "binding_refs" in args and pdk_bindings is None:
        raise CircuitSpecError("Selected device data expired; bind the selected devices again")
    if pdk_bindings is not None:
        args = pdk_bindings.resolve_request(args)
    if name == "preview_circuit_spec":
        return checked_preview(args, workspace=workspace)
    required = {"spec", "bindings", "preview_digest", "geometry", "layout"}
    if (
        name != "preview_circuit_geometry"
        or not isinstance(args, dict)
        or not required <= set(args)
        or set(args) - required - {"template_use"}
    ):
        missing = sorted(required - set(args))
        extra = sorted(set(args) - required - {"template_use"})
        raise CircuitSpecError("geometry arguments: missing " + ", ".join(missing)
                               + "; unsupported " + ", ".join(extra))
    if "template_use" in args:
        validate(args["template_use"], TEMPLATE_USE, "template_use")
    return _bounded(preview_geometry(**args, workspace=workspace))


def _bounded(result):
    # Leave room for escaped JSON in the existing 1 MiB MCP envelope.
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if len(json.dumps(text, ensure_ascii=False).encode("utf-8")) > 900000:
        raise CircuitSpecError("preview response exceeds transport budget; preview a smaller block")
    return result
