"""Reusable symbol-family geometry with target terminal binding.

Families store one normalized body/stub set and a slot table.  A target
schematic supplies the terminal names, directions and widths; ``family_plan``
rebinds those terminals to the stored slots without copying a symbol view.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .template_coords import is_integer_space
from .template_schema import TemplateError, TemplateUnavailable, digest
from .template_symbol_contacts import GRID, attach_stubs
from .template_symbol_family_layout import (
    bootstrap_rows as make_bootstrap_rows,
)
from .template_symbol_family_layout import (
    center_pg_slots,
    drawn_selection_box,
)
from .template_symbol_labels import extract_labels
from .template_symbol_outline import bounds, point
from .template_symbol_pins import label_positions, separate_side_labels

FAMILY_FORMAT = "sico.symbol.family.v1"
VERSION = "cad.template.symbol.v1"


def _asset(record):
    asset = record.get("assets", {}).get("symbol")
    if not asset or asset.get("instances") or asset.get("mosaics"):
        raise TemplateUnavailable("symbol family requires a captured flat symbol asset")
    return asset


def _body(asset, scale=1):
    unit = 1
    if is_integer_space(asset):
        dbu = asset.get("dbu_per_uu")
        if type(dbu) not in (int, float) or dbu <= 0:
            raise TemplateUnavailable("symbol family asset has no usable database unit")
        unit = dbu
    body, points = [], []
    for shape in asset["shapes"]:
        fig = shape["figure"]
        if fig.get("lpp") != ["device", "drawing"]:
            continue
        kind = fig["objType"]
        if kind in {"line", "polygon"}:
            pts = [point(p, scale, unit) for p in fig["points"]]
            row = [kind, pts, None]
        elif kind in {"rect", "ellipse", "arc"}:
            pts = [point(p, scale, unit) for p in fig["bBox"]]
            row = [
                {"rect": "rectangle"}.get(kind, kind),
                pts,
                [
                    [point(p, scale, unit) for p in fig["ellipseBBox"]],
                    fig["startAngle"],
                    fig["stopAngle"],
                ]
                if kind == "arc"
                else None,
            ]
        else:
            raise TemplateUnavailable("unsupported family body shape: " + str(kind))
        if fig.get("width") not in (None, 0):
            raise TemplateUnavailable("wide family body requires a qualified renderer")
        body.append(row)
        points.extend(pts)
    if not body or len(body) > 512:
        raise TemplateUnavailable("family body missing or exceeds 512 shapes")
    return body, points


def _net(record, port):
    nets = {n["id"]: n for n in record.get("topology", {}).get("nets", [])}
    return nets.get(port.get("net"))


def _side(record, port, role=None):
    if role == "power":
        return "top"
    if role == "ground":
        return "bottom"
    net = _net(record, port)
    if net:
        if net.get("sig_type") == "supply":
            return "top"
        if net.get("sig_type") == "ground":
            return "bottom"
    return "right" if port.get("direction") == "output" else "left"


def build_family(
    record,
    family_id,
    *,
    max_stub=1.0,
    scale=1,
    roles=None,
    pg_layout="source",
    side_overrides=None,
):
    """Build a reusable family from one captured symbol and its schematic ports."""
    roles = roles or {}
    side_overrides = side_overrides or {}
    asset = _asset(record)
    unit = 1
    if is_integer_space(asset):
        dbu = asset.get("dbu_per_uu")
        if type(dbu) not in (int, float) or dbu <= 0:
            raise TemplateUnavailable("symbol family asset has no usable database unit")
        unit = dbu
    body, body_points = _body(asset, scale)
    source_ports = {p["name"]: p for p in asset.get("ports", [])}
    topology_ports = record.get("topology", {}).get("ports", [])
    topology_names = {p["name"] for p in topology_ports}
    if source_ports.keys() - topology_names:
        raise TemplateUnavailable("family symbol has extra ports absent from schematic")
    if set(side_overrides) - topology_names:
        raise TemplateError("family side_overrides reference ports absent from schematic")
    if any(side not in {"left", "right", "top", "bottom"} for side in side_overrides.values()):
        raise TemplateError("family side_overrides must use left/right/top/bottom")
    labels = extract_labels(
        asset,
        set(source_ports),
        scale=scale,
        unit=unit,
        policy="preserve",
    )

    anchors = {}
    for pin in asset.get("pins", []):
        name = pin["terminal"]
        if name not in source_ports or name in anchors:
            raise TemplateUnavailable("family requires one pin per source terminal")
        box = [point(value, scale, unit) for value in pin["figure"]["bBox"]]
        anchors[name] = [round((box[0][i] + box[1][i]) / 2, 8) for i in (0, 1)]
    if set(anchors) != set(source_ports):
        raise TemplateUnavailable("family terminal has no captured pin")

    # Source pin stubs are regenerated from the slot anchors; keeping them in
    # the body would duplicate the terminal geometry.
    anchor_points = {tuple(value) for value in anchors.values()}
    filtered = [
        row
        for row in body
        if not (
            row[0] == "line"
            and any(tuple(point_value) in anchor_points for point_value in (row[1][0], row[1][-1]))
        )
    ]
    if not filtered:
        raise TemplateUnavailable("family body is empty after removing pin stubs")
    body = filtered
    body_points = [value for row in body for value in row[1]]
    body_box = bounds(body_points)

    sides = {}
    for port in topology_ports:
        name = port["name"]
        side = side_overrides.get(name, _side(record, port, roles.get(name)))
        if name not in anchors:
            center = [(body_box[0][i] + body_box[1][i]) / 2 for i in (0, 1)]
            if side == "top":
                anchors[name] = [center[0], body_box[1][1] + 0.3125]
            elif side == "bottom":
                anchors[name] = [center[0], body_box[0][1] - 0.3125]
            elif side == "right":
                anchors[name] = [body_box[1][0] + 0.3125, center[1]]
            else:
                anchors[name] = [body_box[0][0] - 0.3125, center[1]]
        sides[name] = side

    stubs = attach_stubs(body, body_box, anchors, sides, max_stub=max_stub, contact=True)
    slots = []
    for port in topology_ports:
        net = _net(record, port)
        slots.append(
            {
                "name": port["name"],
                "direction": port["direction"],
                "num_bits": port["num_bits"],
                "sig_type": net.get("sig_type") if net else "signal",
                "role": (
                    "power"
                    if net and net.get("sig_type") == "supply"
                    else "ground"
                    if net and net.get("sig_type") == "ground"
                    else "signal"
                ),
                "layout_role": roles.get(port["name"], "signal"),
                "side": sides[port["name"]],
                "anchor": anchors[port["name"]],
            }
        )
    label_points = [point for annotation in labels["annotations"] for point in annotation["bbox"]]
    family = {
        "format": FAMILY_FORMAT,
        "family_id": family_id,
        "version": 1,
        "source": {
            "template_ref": record.get("template_ref"),
            "lib": record.get("source", {}).get("lib"),
            "cell": record.get("source", {}).get("cell"),
        },
        "body": body,
        "stubs": stubs,
        "body_box": body_box,
        "selection_box": bounds(body_points + list(anchors.values()) + label_points),
        "slots": slots,
        "grid": GRID,
        "font_height": 0.0625,
        "max_stub": max_stub,
        "annotations": labels["annotations"],
        "annotation_overlaps": labels["overlaps"],
        "dropped_labels": labels["dynamic"],
        "label_expression_policy": "literal_only; dynamic_rejected; no_evaluator",
    }
    if pg_layout != "source":
        family["pg_layout"] = pg_layout
    return family


def _role(sig_type):
    if sig_type == "supply":
        return "power"
    if sig_type == "ground":
        return "ground"
    return "signal"


def target_slot_signature(record):
    """Terminal shape of a target schematic, ignoring names and ordering."""
    result = []
    nets = {n["id"]: n for n in record.get("topology", {}).get("nets", [])}
    for port in record.get("topology", {}).get("ports", []):
        net = nets.get(port.get("net"), {})
        result.append(
            (
                port.get("direction"),
                port.get("num_bits"),
                _role(net.get("sig_type")),
            )
        )
    return tuple(sorted(result, key=str))


def family_slot_signature(family):
    return tuple(
        sorted(
            (
                (
                    slot.get("direction"),
                    slot.get("num_bits"),
                    slot.get("role", "signal"),
                )
                for slot in family.get("slots", [])
            ),
            key=str,
        )
    )


def family_signature(family):
    """Stable identity of reusable geometry and terminal shape."""
    return "fam_" + digest(
        {
            "body": family.get("body"),
            "stubs": family.get("stubs"),
            "slots": family_slot_signature(family),
            "pg_layout": family.get("pg_layout", "source"),
            "annotations": family.get("annotations", []),
            "dropped_labels": family.get("dropped_labels", []),
        }
    )


def dedupe_families(families):
    """Keep one canonical family per geometry/slot signature and list aliases."""
    groups = defaultdict(list)
    for family in families:
        groups[family_signature(family)].append(family)
    result = []
    for signature, items in sorted(groups.items()):
        items = sorted(
            items,
            key=lambda item: (
                str(item.get("family_id") or ""),
                str((item.get("source") or {}).get("lib") or ""),
                str((item.get("source") or {}).get("cell") or ""),
                str((item.get("source") or {}).get("template_ref") or ""),
            ),
        )
        canonical = dict(items[0])
        canonical["signature"] = signature
        canonical["aliases"] = [
            {
                "family_id": item.get("family_id"),
                "source": item.get("source"),
            }
            for item in items
        ]
        result.append(canonical)
    return result


def _cell_stem(cell):
    return re.sub(r"x\d+$", "", str(cell).lower())


def _hint_matches(cell, hint):
    stem, hint = _cell_stem(cell), str(hint).lower()
    if not hint:
        return False
    if stem == hint or stem.startswith(hint + "_") or stem.startswith(hint + "-"):
        return True
    for match in re.finditer(re.escape(hint), stem):
        start, end = match.span()
        left = start == 0 or not stem[start - 1].isalnum()
        right = end == len(stem) or not stem[end].isalnum()
        if left and right:
            return True
    return False


def select_family(record, families, hints):
    """Choose one family by cell hint and terminal shape; never guess ambiguously."""
    cell = str(record.get("source", {}).get("cell", "")).lower()
    wanted = target_slot_signature(record)
    matches = []
    for family_id, family in families.items():
        if family_slot_signature(family) != wanted:
            continue
        if not any(_hint_matches(cell, hint) for hint in hints.get(family_id, [])):
            continue
        matches.append(family_id)
    if len(matches) == 1:
        return matches[0], families[matches[0]]
    if len(matches) > 1:
        raise TemplateError("ambiguous symbol family: " + ", ".join(sorted(matches)))
    return None


def family_plan(record, family, args):
    """Bind target schematic terminals to a stored family and return a plan."""
    if family.get("format") != FAMILY_FORMAT or not isinstance(family.get("slots"), list):
        raise TemplateError("unsupported symbol family record")
    topology = record.get("topology")
    if not topology:
        raise TemplateUnavailable("symbol family requires a schematic topology")
    label_policy = args.get("label_policy", "strict")
    family_dropped = family.get("dropped_labels", [])
    if family_dropped and label_policy in {"strict", "preserve"}:
        message = (
            "source symbol labels are not rendered: "
            if label_policy == "strict"
            else "source symbol dynamic labels require explicit replacement: "
        )
        raise TemplateUnavailable(message + ", ".join(family_dropped[:8]))
    ports = {p["name"]: p for p in topology.get("ports", [])}
    slot_names = {slot["name"] for slot in family["slots"]}
    slot_map = args.get("slot_map") or {}
    if set(slot_map.values()) - slot_names or set(slot_map) - set(ports):
        raise TemplateError("slot_map references unknown target port or family slot")

    target_for_slot = {}
    for target_name, slot_name in slot_map.items():
        if slot_name in target_for_slot.values():
            raise TemplateError("slot_map binds one family slot more than once")
        target_for_slot[target_name] = slot_name
    for slot in family["slots"]:
        if slot["name"] in ports and slot["name"] not in target_for_slot.values():
            target_for_slot[slot["name"]] = slot["name"]

    bound = {slot_name: target for target, slot_name in target_for_slot.items()}
    anchors = {slot["name"]: list(slot["anchor"]) for slot in family["slots"]}
    sides = {slot["name"]: slot["side"] for slot in family["slots"]}
    stubs = family["stubs"]
    if family.get("pg_layout") == "center":
        pg_names = {
            slot["name"]
            for slot in family["slots"]
            if slot.get("layout_role") in {"power", "ground"}
        }
        old_pg = {name: list(anchors[name]) for name in pg_names}
        center_pg_slots(family, anchors, sides)
        pg_anchors = {name: anchors[name] for name in pg_names}
        pg_sides = {name: sides[name] for name in pg_names}
        pg_stubs = attach_stubs(
            family["body"], family["body_box"], pg_anchors, pg_sides, contact=True
        )
        anchors.update(pg_anchors)
        sides.update(pg_sides)
        stubs = [
            row
            for row in stubs
            if not any(point in old_pg.values() for point in (row[1][0], row[1][-1]))
        ] + pg_stubs
    rows, mapped, pins, labels = [], [], [], []
    for slot in family["slots"]:
        target_name = bound.get(slot["name"])
        if target_name is None:
            raise TemplateUnavailable("family slot is not bound: " + slot["name"])
        port = ports[target_name]
        if (port["direction"], port["num_bits"]) != (slot["direction"], slot["num_bits"]):
            raise TemplateUnavailable("family slot direction/width differs: " + slot["name"])
        net = _net(record, port)
        if not net or type(net.get("is_global")) is not bool:
            raise TemplateUnavailable("family target net semantics are incomplete")
        global_net = net.get("source_name") if net.get("is_global") else None
        anchor = anchors[slot["name"]]
        side = sides[slot["name"]]
        right = side == "right"
        rows.append(
            [
                target_name,
                slot["direction"],
                slot["num_bits"],
                global_net,
                slot["sig_type"],
                anchor[0],
                anchor[1],
                right,
            ]
        )
        mapped.append(
            {
                "name": target_name,
                "source_name": slot["name"],
                "direction": slot["direction"],
                "num_bits": slot["num_bits"],
                "sig_type": slot["sig_type"],
                "global_net": global_net,
            }
        )
        pins.append(
            {
                "name": target_name,
                "origin": anchor,
                "right": right,
                "side": side,
                "role": slot.get("role", "signal"),
                "added": slot["name"] not in {p["name"] for p in topology.get("ports", [])},
            }
        )
        labels.append([target_name, *label_positions(anchor[0], anchor[1], side)])
    separate_side_labels(labels, pins, family.get("font_height", 0.0625))
    bootstrap_rows, bootstrap_height = make_bootstrap_rows(rows)
    annotations = [] if label_policy == "drop" else family.get("annotations", [])
    selection_box = (
        drawn_selection_box(family["body"], stubs, pins, annotations) or family["selection_box"]
    )
    plan = {
        "schema_version": VERSION,
        "template_ref": record.get("template_ref"),
        "family_ref": family.get("family_id"),
        "target": [args["library"], args["cell"], "symbol"],
        "title": args["cell"],
        "source": {k: record.get("source", {}).get(k) for k in ("lib", "cell", "library_path")},
        "port_map": args.get("port_map", {}),
        "slot_map": slot_map,
        "ports": mapped,
        "rows": rows,
        "bootstrap_rows": bootstrap_rows,
        "body": family["body"],
        "stubs": stubs,
        "body_box": family["body_box"],
        "selection_box": selection_box,
        # Native preview reserves a left/right bootstrap canvas. Only these
        # dimensions size the disposable bootstrap; selection_box is drawn.
        "width": 1.0,
        "height": bootstrap_height,
        "outline_pins": pins,
        "pin_labels": labels,
        "grid": family.get("grid", GRID),
        "font_height": family.get("font_height", 0.0625),
        "font": "stick",
        "pin_policy": "family_slots",
        "pg_layout": family.get("pg_layout", "source"),
        "style": "symbol_family",
        "requested_style": args.get("requested_style", args.get("style", "symbol_family")),
        "fallback_reason": None,
        "label_policy": label_policy,
        "annotations": annotations,
        "annotation_count": len(annotations),
        "annotation_overlaps": (
            [] if label_policy == "drop" else family.get("annotation_overlaps", [])
        ),
        "dropped_labels": family_dropped,
        "label_expression_policy": family.get(
            "label_expression_policy", "literal_only; dynamic_rejected; no_evaluator"
        ),
        "qualification": "family_geometry_and_target_terminal_binding; visual_review_required",
    }
    return {**plan, "preview_digest": digest(plan)}
