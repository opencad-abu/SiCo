"""Persistent reference adaptation into the shared generic circuit preview contract."""

from __future__ import annotations

import copy
import math
import sqlite3

from .circuit_schema import tool
from .circuit_spec_plan import preview_circuit
from .circuit_spec_schema import MAX_INPUT_BYTES, CircuitSpecError, canonical, digest, validate
from .template_adapt_schema import REQUEST as ADAPT_REQUEST
from .template_adapt_schema import REQUEST_VERSION, device_rows, is_v2
from .template_catalog import TemplateCatalog
from .template_circuit_map import build_spec, source_parts, verify_use
from .template_circuit_nets import scope_evidence
from .template_circuit_schema import PREPARE, TEMPLATE_USE
from .template_coords import asset_space, instance_position, is_integer_space
from .template_pg import pg_hints
from .template_schema import TemplateUnavailable

TOOL_NAME = "prepare_template_circuit"
TOOLS = [
    tool(
        TOOL_NAME,
        "Convert one stored direct schematic template into an explicit generic circuit spec. "
        "Provide every source device's selected project master, target ID/name and parameters; "
        "source numeric values are never copied or evaluated. Explicit terminal/net/port maps "
        "preserve endpoints and global identities by default. To connect a source global through "
        "its existing boundary port, explicitly set net_scope_map[source_net]='local' and a local "
        "net_map/port_map name; this changes external global semantics and is recorded. "
        "Returns spec, template_use, and the shared "
        "preview_circuit_spec plan with source relative placement and PG role hints "
        "(supply/ground candidates for caller confirmation; target nets stay local and PG "
        "nets become explicit pins). No Virtuoso calls/OA writes; "
        "target geometry, device equivalence, live binding and creation remain unqualified.",
        PREPARE["properties"],
        PREPARE["required"],
    )
]
TOOLS[0]["inputSchema"] = {
    "type": "object", "additionalProperties": True, "oneOf": [PREPARE, ADAPT_REQUEST],
}
TOOLS[0]["description"] += (
    " P2 opt-in: request_schema=cad.template.adapt-request.v2 takes the complete target spec, "
    "explicit mapping and fixed allowed_rule_refs against a v3 reuse contract. "
    "Up to eight whole optional-group omissions or one boundary peripheral group are supported; "
    "all residual instances remain in the target and all evidence is recomputed."
)


def _record(reference, workspace):
    try:
        return TemplateCatalog(workspace=workspace).get(reference)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        raise TemplateUnavailable("template lookup failed: " + str(exc)) from exc


def _point(value):
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 1e12 for v in value)
    )


def _integer_point(value):
    return isinstance(value, list) and len(value) == 2 and all(type(v) is int for v in value)


def _box(value):
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(_point(p) for p in value)
        and all(value[0][i] <= value[1][i] for i in (0, 1))
    )


def placement_reference(record, use, spec=None):
    source = record["assets"].get("schematic", {})
    integer = is_integer_space(source)
    origin = source.get("origin")
    if not _point(origin):
        origin = None
    records = {p["device"]: p for p in source.get("instances", [])}
    instances, gaps = [], []
    for row in device_rows(use, spec):
        item = records.get(row["source_device"], {})
        orient, bbox = item.get("orient"), item.get("bbox")
        if integer:
            relative = instance_position(item)
            if not _integer_point(relative):
                relative = None
            box = [[int(p[i]) for i in (0, 1)] for p in bbox] if _box(bbox) else None
        else:
            xy = item.get("xy")
            relative = [xy[i] - origin[i] for i in (0, 1)] if _point(xy) and origin else None
            box = (
                [[p[i] - origin[i] for i in (0, 1)] for p in bbox]
                if _box(bbox) and origin
                else None
            )
        valid_orient = orient in {"R0", "R90", "R180", "R270", "MX", "MY", "MXR90", "MYR90"}
        if relative is None or not valid_orient or box is None:
            gaps.append({"instance": row["instance"], "code": "source_placement_incomplete"})
        instances.append(
            {
                "id": row["instance"],
                "source_device": row["source_device"],
                "relative_xy": relative,
                "source_orientation": orient if valid_orient else None,
                "source_bbox_relative": box,
            }
        )
    reference = {
        "template_ref": record["template_ref"],
        "source_dbu_per_uu": source.get("dbu_per_uu"),
        "coordinate_space": asset_space(source),
        "instances": instances,
        "gaps": gaps,
        "use": "relative_placement_hints; recompute_with_target_master_geometry",
        "target_coordinates_verified": False,
        "source_wires_reused": False,
    }
    if integer:
        anchor = source.get("anchor") if isinstance(source.get("anchor"), dict) else {}
        reference.update(
            units="source_dbu_integer",
            relative_to="template_anchor",
            source_anchor_dbu=anchor.get("source_xy_dbu")
            if _integer_point(anchor.get("source_xy_dbu"))
            else None,
            rounding=source.get("rounding"),
        )
    else:
        reference.update(
            units="source_user_units",
            relative_to="instance_min_origin",
            source_origin=origin,
        )
    return reference


def attach_template(result, use, workspace=None, record=None):
    """Revalidate source mapping even when a previously returned spec is edited."""
    inputs = {
        "spec": result["plan"]["spec"],
        "bindings": result["plan"]["bindings"],
        "template_use": use,
    }
    if len(canonical(inputs).encode()) > MAX_INPUT_BYTES:
        raise CircuitSpecError("template circuit preview input exceeds 192 KiB")
    validate(use, TEMPLATE_USE, "template_use")
    record = record or _record(use["template_ref"], workspace)
    if record["template_ref"] != use["template_ref"]:
        raise CircuitSpecError("template reference changed")
    if is_v2(use):
        from .template_adapt_preview import attach_adapted

        return attach_adapted(result, use, record)
    out = copy.deepcopy(result)
    plan = out["plan"]
    normalized = verify_use(record, plan["spec"], use, plan["bindings"])
    reference = placement_reference(record, normalized)
    devices, nets, ports = source_parts(record)
    referenced_nets = set(nets)
    unused = [n["id"] for n in record["topology"]["nets"] if n["id"] not in referenced_nets]
    source_evidence = {
        "template_ref": record["template_ref"],
        "capture_sha256": record["capture_sha256"],
        "rule_version": record["rule_version"],
        "source": {k: record["source"].get(k) for k in ("lib", "cell", "view", "library_path")},
        "source_gaps": record["topology"].get("gaps", []),
        "ignored_graphics": record["topology"].get("ignored_graphics", []),
        "omitted_endpoint_free_nets": unused,
        "device_references": [
            {
                "instance": m["instance"],
                "source_device": m["source_device"],
                "source_instance_name": devices[m["source_device"]]["source_name"],
                "source_master": devices[m["source_device"]]["master"],
                "source_role": devices[m["source_device"]]["role"],
                "source_kind": devices[m["source_device"]]["kind"],
                "source_attributes": devices[m["source_device"]]["attributes"],
                "source_property_names": devices[m["source_device"]]["observed_property_names"],
                "selected_master": m["master"],
            }
            for m in normalized["devices"]
        ],
        "mapping_verified": True,
        "mapping_scope": "explicit_saved_direct_endpoints_only",
        "device_equivalence_verified": False,
        "electrical_equivalence_verified": False,
        "multiplicity_mapping_verified": False,
        "source_properties_ref": {
            "template_ref": record["template_ref"],
            "section": "schematic",
            "entity": "properties",
        },
        "parameter_policy": "explicit_target_parameters_only; source_values_not_copied",
    }
    if normalized.get("net_scope_map"):
        source_evidence["net_scope_changes"] = scope_evidence(nets, ports, normalized)
    wire_reference = record["assets"].get("schematic", {}).get("wire_reference")
    if wire_reference is not None:
        source_evidence["wire_reference_digest"] = digest(wire_reference)
    if len(canonical(source_evidence).encode()) > 65536:
        raise TemplateUnavailable("template provenance exceeds preview budget")
    plan["template_use"] = normalized
    plan["template_evidence"] = source_evidence
    plan["placement"]["pg_hints"] = pg_hints(record)
    plan["placement"]["template_reference"] = reference
    plan["placement"]["coordinate_status"] = "template_reference_only; target_geometry_deferred"
    positions = {p["id"]: p for p in reference["instances"]}
    for row in plan["placement"]["instances"]:
        row["reference_placement"] = positions[row["id"]]
    plan["next_requirements"] = list(
        dict.fromkeys(
            [*plan["next_requirements"], "selected_master_semantics_review", "source_gap_review"]
        )
    )
    out["preview_digest"] = digest(plan)
    return out


def prepare_template(args, workspace=None):
    if len(canonical(args).encode()) > MAX_INPUT_BYTES:
        raise CircuitSpecError("template circuit input exceeds 192 KiB")
    validate(args, TOOLS[0]["inputSchema"])
    args = copy.deepcopy(args)
    record = _record(args["template_ref"], workspace)
    if args.get("request_schema") == REQUEST_VERSION:
        from .circuit_generic import _bounded
        from .template_adaptation import adapt

        preview = preview_circuit(args["spec"], args["bindings"])
        use = adapt(record, preview["plan"]["spec"], preview["plan"]["bindings"],
                    args["mapping"], args["allowed_rule_refs"])
        result = attach_template(preview, use, record=record)
        return _bounded({**result, "stage": "template_adapted", "spec": result["plan"]["spec"],
                         "template_use": result["plan"]["template_use"], "pg_hints": {}})
    spec, use = build_spec(record, args)
    # Use the same public preview validation, naming, binding gaps and response budget.
    from .circuit_generic import _bounded

    preview = attach_template(preview_circuit(spec, args["bindings"]), use, record=record)
    return _bounded(
        {
            **preview,
            "stage": "template_adapted",
            "spec": preview["plan"]["spec"],
            "template_use": preview["plan"]["template_use"],
            "pg_hints": preview["plan"]["placement"]["pg_hints"],
        }
    )
