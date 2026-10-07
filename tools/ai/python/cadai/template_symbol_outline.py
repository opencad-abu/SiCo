"""Validated static template outlines; no source expressions or properties are executed."""

from __future__ import annotations

import math

from .template_coords import is_integer_space
from .template_schema import TemplateUnavailable, canonical, digest
from .template_symbol_contacts import GRID, MAX_STUB, attach_stubs, side_of
from .template_symbol_pins import label_positions, supply_pins


def point(value, scale, unit=1):
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(
            type(v) not in (int, float) or not math.isfinite(v) or abs(v * scale / unit) > 256
            for v in value
        )
    ):
        raise TemplateUnavailable("invalid/out-of-range template point")
    return [round(v * scale / unit, 8) for v in value]


def bounds(points):
    return [
        [min(p[i] for p in points) for i in (0, 1)],
        [max(p[i] for p in points) for i in (0, 1)],
    ]


def outline_plan(record, plan, scale=1, roles=None):
    asset = record.get("assets", {}).get("symbol")
    if not asset or asset.get("instances") or asset.get("mosaics"):
        raise TemplateUnavailable("template outline requires a captured flat symbol asset")
    # v2 symbol geometry is integer source DBU relative to the symbol origin;
    # the planned symbol keeps user units, so divide by the captured database unit.
    # Legacy v1 assets already carry user units and stay unchanged.
    unit = 1
    if is_integer_space(asset):
        dbu = asset.get("dbu_per_uu")
        if type(dbu) not in (int, float) or not math.isfinite(dbu) or dbu <= 0:
            raise TemplateUnavailable("template symbol asset has no usable database unit")
        unit = dbu
    ports = {p["source_name"]: p for p in plan["ports"]}
    source_ports = {p["name"]: p for p in asset["ports"]}
    if set(source_ports) - set(ports):
        raise TemplateUnavailable("template symbol has extra ports absent from schematic")
    anchors = {}
    for pin in asset["pins"]:
        name = pin["terminal"]
        if name not in source_ports or name in anchors:
            raise TemplateUnavailable("template outline requires one pin per source terminal")
        box = [point(p, scale, unit) for p in pin["figure"]["bBox"]]
        anchors[name] = [round((box[0][i] + box[1][i]) / 2, 8) for i in (0, 1)]
    if set(anchors) != set(source_ports):
        raise TemplateUnavailable("template terminal has no captured pin")
    for name, p in source_ports.items():
        if p["direction"] != ports[name]["direction"] or p["numBits"] != ports[name]["num_bits"]:
            raise TemplateUnavailable(
                "source symbol/schematic port direction or width differs: " + name
            )
    body, stubs, body_points = [], [], []
    for shape in asset["shapes"]:
        fig = shape["figure"]
        # Old selection boxes, pin markers and evaluated labels are regenerated.
        if fig.get("lpp") != ["device", "drawing"]:
            continue
        kind = fig["objType"]
        if kind in {"line", "polygon"}:
            pts = [point(p, scale, unit) for p in fig["points"]]
            if len(pts) < (3 if kind == "polygon" else 2) or len(pts) > 512:
                raise TemplateUnavailable("invalid template shape point count")
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
            if kind == "arc" and any(
                type(v) not in (int, float) or not math.isfinite(v) for v in row[2][1:]
            ):
                raise TemplateUnavailable("invalid template arc angles")
        else:
            raise TemplateUnavailable("unsupported device outline shape: " + str(kind))
        if fig.get("width") not in (None, 0):
            raise TemplateUnavailable(
                "wide template outline requires a separately qualified renderer"
            )
        stub = kind == "line" and any(p in anchors.values() for p in (pts[0], pts[-1]))
        (stubs if stub else body).append(row)
        if not stub:
            body_points.extend(pts)
    if not body or len(body) + len(stubs) > 512:
        raise TemplateUnavailable("template body missing or exceeds 512 shapes")
    body_box = bounds(body_points)
    originally_missing = {
        p["source_name"] for p in plan["ports"] if p["source_name"] not in anchors
    }
    resolved_roles, supply_sides = supply_pins(plan["ports"], anchors, body_box, stubs, roles or {})
    missing = [p for p in plan["ports"] if p["source_name"] not in anchors]
    sides = {name: supply_sides.get(name, side_of(xy, body_box)) for name, xy in anchors.items()}
    # Missing signal inputs go left/below, bidirectional pins left/above;
    # explicit power/ground roles have already selected top/bottom.
    missing_inputs = [p for p in missing if p["direction"] == "input"]
    missing_outputs = [p for p in missing if p["direction"] == "output"]
    missing_bidir = [p for p in missing if p["direction"] == "inputOutput"]
    body_height = body_box[1][1] - body_box[0][1]
    for p in missing_inputs:
        name = p["source_name"]
        anchors[name] = [body_box[0][0] - MAX_STUB, body_box[0][1]]
        sides[name] = "left"
    for index, p in enumerate(missing_outputs, 1):
        y = body_box[0][1] + body_height * index / (len(missing_outputs) + 1)
        x = body_box[1][0]
        anchors[p["source_name"]] = [x + MAX_STUB, y]
        sides[p["source_name"]] = "right"
    for p in missing_bidir:
        name = p["source_name"]
        anchors[name] = [body_box[0][0] - MAX_STUB, body_box[1][1]]
        sides[name] = "left"
    # Source outlines can contain long captured terminal graphics. Rebuild the
    # actual terminal stubs from their body contacts, with one consistent cap.
    stubs = attach_stubs(body, body_box, anchors, sides)
    if len(body) + len(stubs) > 512:
        raise TemplateUnavailable("normalized symbol exceeds 512 shapes")
    rows, pins, labels = [], [], []
    for p in plan["ports"]:
        xy = anchors[p["source_name"]]
        side = sides[p["source_name"]]
        right = side == "right"
        rows.append(
            [p["name"], p["direction"], p["num_bits"], p["global_net"], p["sig_type"], *xy, right]
        )
        pins.append(
            dict(
                name=p["name"],
                origin=xy,
                right=right,
                side=side,
                role=resolved_roles[p["source_name"]],
                added=p["source_name"] in originally_missing,
            )
        )
        labels.append([p["name"], *label_positions(*xy, side)])
    box = bounds(body_points + list(anchors.values()))
    if box[0][0] >= box[1][0] or box[0][1] >= box[1][1]:
        raise TemplateUnavailable("template selection box is degenerate")
    plan = {
        **plan,
        "style": "template_outline",
        "outline_scale": scale,
        "bootstrap_rows": plan["rows"],
        "rows": rows,
        "body": sorted(body, key=canonical),
        "stubs": sorted(stubs, key=canonical),
        "body_box": body_box,
        "selection_box": box,
        "outline_pins": pins,
        "added_ports": [p["name"] for p in plan["ports"] if p["source_name"] in originally_missing],
        "port_roles": roles or {},
        "pin_labels": labels,
        "font_height": 0.0625,
        "font": "stick",
        "outline_source": asset["source"],
        "fixed_annotations": [
            "cdsName():ILLabel",
            "[@partName]:NLPLabel",
            "[@cellName]:NLPLabel",
            "cdsTerm(terminal_name):ILLabel",
        ],
        "pin_policy": "power_top; ground_bottom; signal_side_preserved; grid_pins_touch_body",
        "stub_grid": GRID,
        "stub_max": MAX_STUB,
        "qualification": "template_outline_and_full_schematic_interface; visual_review_required",
    }
    plan.pop("preview_digest", None)
    if len(canonical(plan).encode()) > 55000:
        raise TemplateUnavailable("outline plan exceeds transport budget")
    return {**plan, "preview_digest": digest(plan)}


def outline_shapes(plan):
    """Same geometry as the native builder, with dynamic labels displayed literally."""
    shapes = []
    for kind, pts, ellipse in plan["body"] + plan["stubs"]:
        fig = {"objType": {"rectangle": "rect"}.get(kind, kind), "bBox": bounds(pts)}
        if kind in {"line", "polygon"}:
            fig["points"] = pts
        if kind == "arc":
            # dbCreateArc uses ellipse/arc boxes. Render the same ellipse section.
            fig.update(ellipseBBox=ellipse[0], startAngle=ellipse[1], stopAngle=ellipse[2])
        shapes.append({"figure": fig})

    def label(text, xy, justify):
        shapes.append(
            {
                "figure": dict(
                    objType="label",
                    text=text,
                    xy=xy,
                    height=0.0625,
                    font=plan["font"],
                    justify=justify,
                )
            }
        )

    for p, labels in zip(plan["outline_pins"], plan["pin_labels"]):
        x, y = p["origin"]
        shapes.append(
            {"figure": dict(objType="rect", bBox=[[x - 0.025, y - 0.025], [x + 0.025, y + 0.025]])}
        )
        label(p["name"], labels[1], labels[2])
        label(
            'cdsTerm("' + p["name"] + '")',
            labels[3],
            labels[4],
        )
    lo, hi = plan["selection_box"]
    label("cdsName()", [lo[0], hi[1] + 0.125], "lowerLeft")
    label("[@partName]", [(lo[i] + hi[i]) / 2 for i in (0, 1)], "centerCenter")
    label("[@cellName]", [hi[0], hi[1] + 0.125], "lowerRight")
    return shapes
