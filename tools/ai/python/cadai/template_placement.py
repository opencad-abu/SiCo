"""Deterministic target placement from a stored template's relative relations.

The planner preserves evidenced source tracks, mirrored orientations and port
positions. Source pitch is a lower bound; target bodies, text proxies and grid
determine final distances. The result is an
ordinary ``layout`` object for ``preview_circuit_geometry`` /
``prepare_circuit_creation``, so digest binding and validation stay unchanged.
"""

from __future__ import annotations

import json

from .circuit_spec_schema import (
    CircuitSpecError,
    digest,
    validate,
)
from .template_adapt_schema import is_v2
from .template_catalog import TemplateCatalog
from .template_placement_layout import (
    plan_layout as plan_layout,
    PLACEMENT_SCHEMA as PLACEMENT_SCHEMA,
    MAX_INSTANCES as MAX_INSTANCES,
    LOOSE_MIN_GAP as LOOSE_MIN_GAP,
)
from .template_placement_schema import (
    DEVICE_MAP as DEVICE_MAP,
    POSITIVE as POSITIVE,
)
from .template_placement_schema import (
    MAX_RESPONSE_BYTES as MAX_RESPONSE_BYTES,
)
from .template_placement_schema import OPTIONS as OPTIONS
from .template_placement_schema import (
    TOOL_NAME as TOOL_NAME,
)
from .template_placement_schema import (
    TOOL_NAMES as TOOL_NAMES,
)
from .template_placement_schema import (
    TOOLS as TOOLS,
)
from .template_relations import relations
from .template_schema import require_legacy_consumer


def _device_mapping(args):
    """Source template device id -> target instance id."""
    mapping = {}
    use = args.get("template_use")
    if is_v2(use):
        mapping.update(use["device_map"])
    if isinstance(use, dict) and isinstance(use.get("devices"), list):
        for row in use["devices"]:
            source, instance = row.get("source_device"), row.get("instance")
            if not isinstance(source, str) or not isinstance(instance, str):
                raise CircuitSpecError("template_use devices require source_device and instance")
            mapping[source] = instance
    for source, instance in (args.get("device_map") or {}).items():
        mapping[source] = instance
    if not mapping:
        raise CircuitSpecError(
            "placement requires template_use devices or an explicit device_map"
        )
    return mapping


def preview_placement(args, workspace=None, pdk_bindings=None):
    """MCP entry point: resolve the template, plan the layout, return both."""
    if "binding_refs" in args and pdk_bindings is None:
        raise CircuitSpecError("Selected device data expired; bind the selected devices again")
    if pdk_bindings is not None:
        args = pdk_bindings.resolve_request(args, include_geometry=True)
    validate(args, next(entry["inputSchema"] for entry in TOOLS if entry["name"] == TOOL_NAME))
    for key in ("spec", "bindings", "geometry"):
        if key not in args:
            raise CircuitSpecError("preview_template_placement requires " + key)
    catalog = TemplateCatalog(workspace=workspace)
    record = catalog.get(args["template_ref"])
    if is_v2(args.get("template_use")):
        from .circuit_spec_plan import preview_circuit
        from .template_circuit import attach_template

        checked = attach_template(preview_circuit(args["spec"], args["bindings"]),
                                  args["template_use"], record=record)
        if args.get("device_map") and args["device_map"] != args["template_use"]["device_map"]:
            raise CircuitSpecError("device_map differs from verified template_use")
        if "port_map" in args and args["port_map"] != args["template_use"]["port_map"]:
            raise CircuitSpecError("port_map differs from verified template_use")
        if ("terminal_map" in args
                and args["terminal_map"] != args["template_use"]["terminal_map"]):
            raise CircuitSpecError("terminal_map differs from verified template_use")
        args = {**args, "spec": checked["plan"]["spec"]}
    else:
        require_legacy_consumer(record)
    relation_view = relations(record)
    use = args.get("template_use") or {}
    terms = use.get("terminal_map") if is_v2(use) else {
        d["source_device"]: d["terminal_map"] for d in use.get("devices", [])}
    if terms and "terminal_map" in args and args["terminal_map"] != terms:
        raise CircuitSpecError("terminal_map differs from template_use")
    layout, report = plan_layout(
        args["spec"],
        args["bindings"],
        args["geometry"],
        relation_view,
        _device_mapping(args),
        args.get("options"),
        port_map=args.get("port_map", (args.get("template_use") or {}).get("port_map")),
        terminal_map=args.get("terminal_map", terms or None),
    )
    result = {
        "ok": True,
        "stage": "template_placement",
        "status": "placement_planned",
        "next_action": "preview_circuit_geometry",
        "user_input_required": False,
        "layout": layout,
        "report": report,
        "placement_digest": digest(layout),
        "template": {
            "template_ref": record.get("template_ref"),
            "library": record["source"]["lib"],
            "cell": record["source"]["cell"],
        },
        "target": args["spec"]["target"],
    }
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if len(json.dumps(text, ensure_ascii=False).encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise CircuitSpecError("placement response exceeds transport budget")
    return result
