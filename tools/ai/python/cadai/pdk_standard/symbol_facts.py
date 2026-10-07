"""Conservative geometry from saved static flat OA symbols, without callbacks."""

from collections import defaultdict

from ..pdk_normalize import raw_value
from .constraints import numeric
from .geometry import _box, _union, pending

METHOD = "sico-static-symbol-facts-v1"


def _point(value):
    return isinstance(value, list) and len(value) == 2 and all(numeric(x) for x in value)


def _rectangular(raw, box):
    if not box or any(box[0][k] >= box[1][k] for k in (0, 1)):
        return False
    kind = raw_value(raw.get("objType"))
    if kind == "rect":
        return True
    if kind != "polygon":
        return False
    points = raw_value(raw.get("points"))
    if not isinstance(points, list) or not all(_point(p) for p in points):
        return False
    if len(points) == 5 and points[0] == points[-1]:
        points = points[:-1]
    corners = {(x, y) for x in (box[0][0], box[1][0]) for y in (box[0][1], box[1][1])}
    # Corner membership alone would accept a self-intersecting bow-tie.
    return (
        len(points) == 4
        and set(map(tuple, points)) == corners
        and all((a[0] == b[0]) != (a[1] == b[1]) for a, b in zip(points, points[1:] + points[:1]))
    )


def _centers(geometry, terminals):
    grouped = defaultdict(list)
    for fig in geometry.get("items", []):
        grouped[(fig["terminal"], fig["pin_index"])].append(fig)
    result = {}
    for name, count in terminals.items():
        points = []
        for index in range(count):
            figures = grouped.get((name, index), [])
            if not figures:
                break
            centers = []
            for fig in figures:
                raw = fig["raw"]
                box = _box(raw_value(raw.get("bBox")))
                if fig.get("status") != "complete" or not _rectangular(raw, box):
                    break
                centers.append([(box[0][k] + box[1][k]) / 2 for k in (0, 1)])
            if len(centers) != len(figures) or any(p != centers[0] for p in centers):
                break
            points.append(centers[0])
        if count > 0 and len(points) == count:
            result[name] = points
    return result


def _escape(point, bbox, shapes):
    boundary = set()
    for k, lo, hi in ((0, "left", "right"), (1, "down", "up")):
        if point[k] == bbox[0][k]:
            boundary.add(lo)
        if point[k] == bbox[1][k]:
            boundary.add(hi)
    if len(boundary) == 1:
        return next(iter(boundary))
    # A corner has two outward normals. Select only a unique straight lead
    # ending at the pin; no left/right priority heuristic.
    leads = set()
    for shape in shapes:
        if raw_value(shape.get("objType")) != "line":
            continue
        points = raw_value(shape.get("points"))
        if not isinstance(points, list) or len(points) < 2 or not all(_point(p) for p in points):
            continue
        for end, adjacent in ((points[0], points[1]), (points[-1], points[-2])):
            if end != point:
                continue
            dx, dy = point[0] - adjacent[0], point[1] - adjacent[1]
            if dx and not dy:
                leads.add("right" if dx > 0 else "left")
            if dy and not dx:
                leads.add("up" if dy > 0 else "down")
    directions = boundary & leads
    return next(iter(directions)) if len(directions) == 1 else None


def derive(value):
    g = value["geometry"]
    result = {"bbox": pending("Saved static flat body geometry required"), "terminals": {}}
    if (value.get("master_state") or {}).get("modified") is not False:
        return result
    if (
        g.get("parameterized_master") is not False
        or g.get("hierarchical_instance_count") != 0
        or g.get("mosaic_count") != 0
        or g.get("body_outline_status") != "complete"
        or g.get("truncated")
        or not isinstance(g.get("body_shapes"), list)
    ):
        return result
    terminals = {p["name"]: p.get("pin_count", 0) for p in value["ports"]["items"]}
    centers = _centers(g, terminals)
    if set(centers) != set(terminals):
        result["bbox"] = pending(
            "All pin centers must be validated before deriving occupied bounds"
        )
        return result
    boxes = [_box(raw_value(s.get("bBox"))) for s in g["body_shapes"]]
    if any(b is None for b in boxes):
        result["bbox"] = pending("Body shape bounds unavailable")
        return result
    boxes += [[p, p] for points in centers.values() for p in points]
    body = _union(boxes)
    if not body or any(body[0][k] >= body[1][k] for k in (0, 1)):
        result["bbox"] = pending("Positive two-dimensional occupied bounds required")
        return result
    result["bbox"] = body
    for name, points in centers.items():
        anchors = [
            {"id": "pin" + str(i), "xy": p, "escape": _escape(p, body, g["body_shapes"])}
            for i, p in enumerate(points)
        ]
        result["terminals"][name] = (
            anchors
            if all(a["escape"] for a in anchors)
            else pending("Pin has no unique outward escape; source or user review required")
        )
    return result
