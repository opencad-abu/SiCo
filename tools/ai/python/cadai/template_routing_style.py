"""Derived routing-style hints; independent of placement relation evidence."""

from .template_coords import instance_position, is_integer_space
from .template_coords import is_point as _point
from .template_coords import valid_box as _box
from .template_relation_geometry import _net_names
from .template_schema import TemplateUnavailable


def _schematic(record):
    asset = record.get("assets", {}).get("schematic")
    if not asset:
        raise TemplateUnavailable("this derived view requires a stored schematic asset")
    return asset

STYLE_SCHEMA = "cad.template.routing_style.v1"
MAX_NETS = 256
ATTACH_TOLERANCE = 10

def _figures(asset, kind):
    return [
        shape
        for shape in asset.get("shapes", [])
        if (shape.get("figure") or {}).get("objType") == kind
    ]


def _junction_points(junctions):
    points = []
    for shape in junctions:
        figure = shape.get("figure") or {}
        box = figure.get("ellipseBBox") or figure.get("bBox")
        if _box(box):
            points.append(((box[0][0] + box[1][0]) / 2, (box[0][1] + box[1][1]) / 2))
    return points


def _boxes(asset):
    boxes = []
    for row in asset.get("instances", []):
        if _point(instance_position(row)) and _box(row.get("bbox")):
            boxes.append(row["bbox"])
    return boxes


def _inside(px, py, box, tolerance):
    return (
        box[0][0] - tolerance <= px <= box[1][0] + tolerance
        and box[0][1] - tolerance <= py <= box[1][1] + tolerance
    )


def routing_style(record):
    """Wire channels, branch shape and label usage of one stored schematic."""
    asset = _schematic(record)
    lines = _figures(asset, "line")
    labels = _figures(asset, "label")
    junctions = _junction_points(_figures(asset, "ellipse"))
    boxes = _boxes(asset)
    names = _net_names(record)
    terminals = {}
    for device in record.get("topology", {}).get("devices", []):
        for pin in device.get("pins", []):
            if pin.get("net"):
                net = names.get(pin["net"], pin["net"])
                terminals[net] = terminals.get(net, 0) + 1

    nets, orthogonal, points_per_segment = {}, 0, {}
    attachment = {"on_instance_bbox": 0, "on_junction": 0, "other": 0}
    junction_hits = {point: [] for point in junctions}
    for shape in lines:
        figure = shape.get("figure") or {}
        points = figure.get("points")
        if not isinstance(points, list) or len(points) < 2:
            continue
        points_per_segment[len(points)] = points_per_segment.get(len(points), 0) + 1
        net = shape.get("net")
        entry = nets.setdefault(
            net,
            {
                "net": net,
                "segments": 0,
                "labels": 0,
                "junctions": 0,
                "rows": set(),
                "columns": set(),
                "degrees": {},
            },
        )
        entry["segments"] += 1
        if abs(points[0][0] - points[-1][0]) <= 1 or abs(points[0][1] - points[-1][1]) <= 1:
            orthogonal += 1
        if abs(points[0][1] - points[-1][1]) <= 1:
            entry["rows"].add(points[0][1])
        if abs(points[0][0] - points[-1][0]) <= 1:
            entry["columns"].add(points[0][0])
        for point in (points[0], points[-1]):
            key = (point[0], point[1])
            entry["degrees"][key] = entry["degrees"].get(key, 0) + 1
            for junction in junction_hits:
                if abs(point[0] - junction[0]) <= 1 and abs(point[1] - junction[1]) <= 1:
                    junction_hits[junction].append(net)
                    break
            else:
                if any(_inside(point[0], point[1], box, ATTACH_TOLERANCE) for box in boxes):
                    attachment["on_instance_bbox"] += 1
                else:
                    attachment["other"] += 1
    for shape in labels:
        entry = nets.get(shape.get("net"))
        if entry is not None:
            entry["labels"] += 1
    for junction, nets_at in junction_hits.items():
        for net in set(nets_at):
            if net in nets:
                nets[net]["junctions"] += 1
        if nets_at:
            attachment["on_junction"] += len(nets_at)
    rows = []
    for net, entry in sorted(nets.items(), key=lambda item: str(item[0])):
        rows.append(
            {
                "net": net,
                "terminals": terminals.get(net, 0),
                "segments": entry["segments"],
                "junctions": entry["junctions"],
                "labels": entry["labels"],
                "rows": sorted(entry["rows"]),
                "columns": sorted(entry["columns"]),
                "branch_degree_max": max(entry["degrees"].values()) if entry["degrees"] else 0,
            }
        )
        if len(rows) >= MAX_NETS:
            break
    return {
        "schema": STYLE_SCHEMA,
        "template_ref": record.get("template_ref"),
        "unit": "source_dbu" if is_integer_space(asset) else "source_user_units",
        "totals": {
            "segments": len(lines),
            "junctions": len(junctions),
            "labels": len(labels),
            "terminals": sum(terminals.values()),
            "orthogonal_segments": orthogonal,
        },
        "points_per_segment": {str(k): v for k, v in sorted(points_per_segment.items())},
        "nets": rows,
        "attachment": attachment,
        "use": "routing_style_reference_only; target wires are recomputed",
    }
