"""Resolve explicit placement and selected pin anchors without querying PDKs or writing OA."""

from .circuit_geometry_checks import geometry_issues
from .circuit_geometry_routing import _route_cadence, _route_end_to_end
from .circuit_geometry_schema import (
    EPS,
    GEOMETRY,
    LAYOUT,
    PLAN_VERSION,
    VECTORS,
    bounds,
    inside,
    on_grid,
    transform,
    transform_box,
)
from .circuit_pin_links import pin_links
from .circuit_pin_style import placed_pin_orientation
from .circuit_port_routing import port_stub_issues
from .circuit_spec_plan import preview_circuit
from .circuit_spec_schema import CircuitSpecError, canonical, digest, unique, validate
from .circuit_stub_labels import reserve_stub_labels

# Wiring is a user decision, not an agent default: the creation flow requires an
# explicit routing.mode, the Copilot question offers "stub" first and a timeout
# falls back to it. The geometry planner still honours an omitted mode for direct
# previews, where the cheapest choice stays the safe one.
DEFAULT_ROUTING_MODE = "stub"


def _matching(plan, geometry, layout):
    validate(geometry, GEOMETRY, "geometry")
    validate(layout, LAYOUT, "layout")
    spec = plan["spec"]
    for key, expected in (
        ("project_ref", spec["project_ref"]),
        ("binding_snapshot", spec["binding_snapshot"]),
        ("binding_digest", plan["binding_digest"]),
    ):
        if geometry[key] != expected:
            raise CircuitSpecError("geometry " + key + " differs from circuit preview")
    blocking = [
        issue
        for issue in plan["issues"]
        if not (issue["code"] == "callback_extension_not_validated" and issue.get("extension_ref"))
    ]
    if blocking:
        raise CircuitSpecError("resolve circuit binding issues before geometry planning")
    # A named project extension can proceed to geometric planning only. Native
    # preparation must find its exact target registration and validate the request.
    rows = unique(geometry["instances"], "instance", "geometry.instances")
    expected = {i["id"] for i in spec["instances"]}
    if set(rows) != expected or set(layout["instances"]) != expected:
        raise CircuitSpecError("geometry and placement must cover exactly the selected instances")
    if set(layout["anchor_choices"]) - expected:
        raise CircuitSpecError("anchor_choices contains unknown instances")
    if set(layout["ports"]) != {p["name"] for p in spec["ports"]}:
        raise CircuitSpecError("placement must cover exactly the declared ports")
    if layout["stub_length"] < layout["grid"] or not on_grid(
        [layout["stub_length"]], layout["grid"]
    ):
        raise CircuitSpecError("stub_length must be at least one grid and a grid multiple")
    return rows


def _anchor(terminal, selected):
    anchors = unique(terminal["anchors"], "id", "terminal.anchors")
    if selected is None:
        if len(anchors) != 1:
            raise CircuitSpecError("multiple pin anchors require explicit anchor_choices")
        return next(iter(anchors.values()))
    if selected not in anchors:
        raise CircuitSpecError("selected pin anchor does not exist")
    return anchors[selected]


def _wire(endpoint, net, xy, escape, length, grid):
    end = [xy[k] + escape[k] * length for k in (0, 1)]
    if not on_grid(xy, grid) or not on_grid(end, grid):
        raise CircuitSpecError("wire anchor/endpoint is off grid; no coordinate rounding performed")
    return {
        "endpoint": endpoint,
        "net": net,
        "points": [xy, end],
        "label": {"text": net, "xy": end},
        "escape": [escape[0], escape[1]],
    }


def _instances(plan, geometry, layout):
    routing_mode = (layout.get("routing") or {}).get("mode", DEFAULT_ROUTING_MODE)
    masters = {m["id"]: m for m in plan["bindings"]["masters"]}
    placed, pins, wires, interior = {}, [], [], []
    for instance in plan["spec"]["instances"]:
        key = instance["id"]
        source, position = geometry[key], layout["instances"][key]
        master = masters[instance["master"]]
        if source["master_revision"] != master["revision"]:
            raise CircuitSpecError("geometry master revision differs: " + key)
        if source["parameters_digest"] != digest(instance["parameters"]):
            raise CircuitSpecError("geometry requested-parameter digest differs: " + key)
        if not on_grid(position["xy"], layout["grid"]):
            raise CircuitSpecError("instance origin is off grid: " + key)
        box = transform_box(source["occupied_bbox"], position["orientation"], position["xy"])
        placed[key] = {
            "id": key,
            "name": instance["name"],
            "role": instance["role"],
            "master": instance["master"],
            **position,
            "bbox": box,
        }
        terminals = unique(source["terminals"], "name", "geometry.terminals")
        if set(terminals) != {t["name"] for t in master["terminals"]}:
            raise CircuitSpecError("geometry terminals differ from selected master: " + key)
        choices = layout["anchor_choices"].get(key, {})
        if set(choices) - set(instance["connections"]):
            raise CircuitSpecError("anchor_choices may only select connected terminals")
        for name, term in terminals.items():
            unique(term["anchors"], "id", "terminal.anchors")
            endpoint = {"instance": key, "terminal": name}
            for candidate in term["anchors"]:
                if not inside(candidate["xy"], source["occupied_bbox"]):
                    raise CircuitSpecError("pin anchor is outside supplied occupied_bbox")
                pins.append(
                    {
                        "endpoint": endpoint,
                        "net": instance["connections"].get(name),
                        "xy": transform(candidate["xy"], position["orientation"], position["xy"]),
                    }
                )
            if name not in instance["connections"]:
                continue
            selected = _anchor(term, choices.get(name))
            direction = VECTORS[selected["escape"]]
            # Require an outward ray from a boundary pin, never a guessed path through a body.
            beyond = [selected["xy"][k] + direction[k] * EPS * 4 for k in (0, 1)]
            if inside(beyond, source["occupied_bbox"]):
                if routing_mode != "end_to_end":
                    raise CircuitSpecError(
                        "stub escape must lead out of occupied_bbox; explicit routing needed"
                    )
                # Routed plans can start from an interior anchor (for example a
                # PDK bulk pin drawn inside the body): the router owns the path,
                # and the owner body stays exempt for the first segment.
                box = source["occupied_bbox"]
                centre = [(box[0][k] + box[1][k]) / 2 for k in (0, 1)]
                offset = [selected["xy"][k] - centre[k] for k in (0, 1)]
                if abs(offset[1]) >= abs(offset[0]):
                    suggested = "up" if offset[1] > 0 else "down"
                else:
                    suggested = "right" if offset[0] > 0 else "left"
                interior.append(
                    {
                        "instance": key,
                        "terminal": name,
                        "anchor": selected["id"],
                        "suggested_escape": suggested,
                    }
                )
            xy = transform(selected["xy"], position["orientation"], position["xy"])
            vector = transform(direction, position["orientation"])
            wire = _wire(
                endpoint,
                instance["connections"][name],
                xy,
                vector,
                layout["stub_length"],
                layout["grid"],
            )
            wire["anchor_id"] = selected["id"]
            wires.append(wire)
    return placed, pins, wires, interior


def preview_geometry(
    spec, bindings, preview_digest, geometry, layout, *, template_use=None, workspace=None
):
    if len(canonical({"geometry": geometry, "layout": layout}).encode("utf-8")) > 196608:
        raise CircuitSpecError("geometry input exceeds 192 KiB")
    preview = preview_circuit(spec, bindings)
    if template_use is not None:
        from .template_circuit import attach_template

        preview = attach_template(preview, template_use, workspace=workspace)
    if preview_digest != preview["preview_digest"]:
        raise CircuitSpecError(
            "preview_digest differs; preview exact spec, bindings and template_use again"
        )
    plan = preview["plan"]
    sources = _matching(plan, geometry, layout)
    placed, pins, wires, interior = _instances(plan, sources, layout)
    # Port pins keep two independent facts: ``escape`` (stub direction, chosen by
    # the plan) and ``orientation`` (pin master rotation, derived here from the
    # terminal direction and the body edge the port faces).  The wire direction
    # never rotates a pin master.
    body = bounds([point for row in placed.values() for point in row["bbox"]]) if placed else None
    ports = []
    for port in plan["spec"]["ports"]:
        position = layout["ports"][port["name"]]
        ports.append(
            {
                **port,
                **position,
                "orientation": placed_pin_orientation(
                    position, body, port["direction"]
                ),
            }
        )
        endpoint = {"port": port["name"]}
        pins.append({"endpoint": endpoint, "net": port["net"], "xy": position["xy"]})
        wires.append(
            _wire(
                endpoint,
                port["net"],
                position["xy"],
                VECTORS[position["escape"]],
                layout["stub_length"],
                layout["grid"],
            )
        )
    wires = pin_links(plan, wires, layout)
    routing = layout.get("routing") or {}
    if routing.get("engine") == "template" and routing.get("mode") != "end_to_end":
        raise CircuitSpecError("template routing requires mode=end_to_end")
    if "fallback" in routing and routing.get("engine") != "template":
        raise CircuitSpecError("fallback is supported only by template routing")
    routing_report = {
        "mode": routing.get("mode", DEFAULT_ROUTING_MODE),
        "port_style": "stub",
        "port_stub_terminals": len(ports),
        "fallbacks": [],
        "junctions": [],
    }
    if routing_report["mode"] == "end_to_end":
        if routing.get("engine") == "template":
            from .template_routing import route_template

            wires, routing_report = route_template(
                plan, sources, layout, wires, placed, pins, workspace=workspace
            )
        elif routing.get("engine", "cadence_route") == "cadence_route":
            wires, routing_report = _route_cadence(plan, wires, layout)
        else:
            wires, routing_report = _route_end_to_end(plan, placed, pins, wires, layout)
    if routing.get("decision"):
        # The wiring choice and where it came from (user answer or the timeout default)
        # travel with the plan so the audit trail never has to guess.
        routing_report["decision"] = routing["decision"]
    if interior:
        routing_report["interior_escapes"] = interior
    wires = reserve_stub_labels(wires, sources, layout)
    layout_review = []
    issues = geometry_issues(
        plan,
        placed,
        ports,
        pins,
        wires,
        layout["clearance"],
        terminal_rows=_terminal_rows(plan, sources, layout),
        grid=layout["grid"],
        review=layout_review,
        port_routing=routing.get("ports", "stub"),
    )
    from .template_adapt_geometry import placement_issues

    issues = (
        routing_report.get("issues", []) + placement_issues(plan, layout, placed) + issues
        + port_stub_issues(plan, layout, body, wires)
    )[:128]
    from .circuit_annotations import TEXT_POLICY, note_text

    notes = [
        {**note, "bbox": bounds([p for key in note["instances"] for p in placed[key]["bbox"]])}
        for note in plan["spec"]["notes"]
    ]
    for note in notes:
        note["rendered_text"] = note_text(note["text"], note["bbox"])
    result = {
        "schema": PLAN_VERSION,
        "preview_digest": preview_digest,
        "geometry_digest": digest(geometry),
        "layout": layout,
        "instances": list(placed.values()),
        "ports": ports,
        "wires": wires,
        "notes": notes,
        "issues": issues,
        "layout_review": layout_review,
        "issues_capped": len(issues) == 128,
        "geometry_checks_passed": not issues,
        "binding_source_verified": False,
        "live_binding_verified": False,
        "creation_ready": False,
        "label_rendering_verified": False,
        "text_layout_policy": TEXT_POLICY,
        "geometry_evidence_kind": geometry["evidence_kind"],
        "routing": routing_report,
        "target": spec["target"],
    }
    if template_use is not None:
        result["template_use"] = plan["template_use"]
        result["template_evidence"] = plan["template_evidence"]
        result["template_placement_reference"] = plan["placement"]["template_reference"]
    return {
        "ok": True,
        "stage": "geometry_preview",
        "status": ("needs_geometry" if issues and routing.get("engine") == "template"
                   else "geometry_conflicts" if issues else "geometry_checked"),
        "next_action": "resolve_geometry_conflicts" if issues else "prepare_circuit_creation",
        "user_input_required": False,
        "geometry_plan_digest": digest(result),
        "plan": result,
    }


def _terminal_rows(plan, geometry_rows, layout):
    """World-space pin line of every connected terminal, grouped by net.

    The port-row check only needs the y of the pin each port would drive; the
    x is decided by the port band and the escape, so it stays out of this map.
    Terminals whose anchors are ambiguous without an explicit choice are skipped:
    the plan already rejects them when the anchor is resolved.
    """
    rows = {}
    choices = layout["anchor_choices"]
    for instance in plan["spec"]["instances"]:
        source = geometry_rows[instance["id"]]
        position = layout["instances"][instance["id"]]
        terminals = {row["name"]: row for row in source["terminals"]}
        for name, net in instance["connections"].items():
            terminal = terminals.get(name)
            if terminal is None:
                continue
            anchors = {row["id"]: row for row in terminal["anchors"]}
            selected = choices.get(instance["id"], {}).get(name)
            if selected in anchors:
                anchor = anchors[selected]
            elif len(anchors) == 1:
                anchor = next(iter(anchors.values()))
            else:
                continue
            point = transform(anchor["xy"], position["orientation"], position["xy"])
            rows.setdefault(net, []).append(
                (point[1], instance["name"] + "." + name)
            )
    return rows
