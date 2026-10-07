"""Read schematic port placement evidence without inferring electrical connectivity."""

from .circuit_geometry_schema import transform
from .circuit_pin_style import OUTWARD, default_side
from .template_coords import is_integer_space, is_point, union_box, valid_box


def _side(xy, box, direction, orientation):
    # Pin graphics are independent of source CDF text extents. Prefer their
    # captured arrow orientation to classifying a pin against a text-heavy bbox.
    if orientation in {"R0", "R90", "R180", "R270"}:
        vector = transform([1 if direction == "output" else -1, 0], orientation)
        return next(side for side, value in OUTWARD.items() if list(value) == vector)
    if box:
        for axis, index, side in ((0, 0, "left"), (0, 1, "right"),
                                  (1, 1, "top"), (1, 0, "bottom")):
            if (xy[axis] - box[index][axis]) * (1 if index else -1) > 0:
                return side
    return default_side(direction)


def port_relations(record):
    """One source-relative position per port; ambiguous evidence stays a gap.

    Capture v2's actual electrical anchor wins. Older records may use the
    origin of a captured pin instance, or the centre of a non-instance figure.
    Multiple physical locations cannot be silently collapsed to one target pin.
    """
    asset = record["assets"]["schematic"]
    reference = asset.get("wire_reference") or {}
    source_box = union_box([i.get("bbox") for i in asset.get("instances", [])])
    origin = asset.get("origin")
    if source_box and not is_integer_space(asset) and is_point(origin):
        source_box = [[p[k] - origin[k] for k in (0, 1)] for p in source_box]
    result = []
    for port in record.get("topology", {}).get("ports", []):
        name = port["name"]
        figures = [p.get("figure") or {} for p in asset.get("pins", [])
                   if p.get("terminal") == name]
        anchors = [p.get("xy") for p in reference.get("ports", []) if p.get("port") == name]
        if not anchors:
            for figure in figures:
                point = figure.get("xy")
                if not is_point(point) and figure.get("objType") != "inst":
                    box = figure.get("bBox")
                    if valid_box(box):
                        point = [(box[0][k] + box[1][k]) / 2 for k in (0, 1)]
                anchors.append(point)
        row = {"name": name, "direction": port.get("direction"), "xy": None}
        if len(anchors) == 1 and is_point(anchors[0]):
            xy = list(anchors[0])
            if not is_integer_space(asset) and is_point(asset.get("origin")):
                xy = [xy[k] - asset["origin"][k] for k in (0, 1)]
            orient = figures[0].get("orient") if len(figures) == 1 else None
            row.update(xy=xy, side=_side(xy, source_box, port.get("direction"), orient))
        else:
            row["gap"] = "template_port_position_missing_or_ambiguous"
        result.append(row)
    return result
