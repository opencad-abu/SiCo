"""Recheck declared placement constraints against every target, including residuals."""

from .circuit_geometry_schema import EPS, transform
from .template_adapt_schema import is_v2


def placement_issues(plan, layout, placed):
    use = plan.get("template_use")
    if not is_v2(use):
        return []
    issues = []
    for constraint in plan["template_evidence"]["placement_constraints"]:
        if constraint["strength"] != "hard":
            continue
        keys = [use["device_map"][d] for d in constraint["devices"] if d in use["device_map"]]
        if len(keys) < 2:
            continue
        positions = [layout["instances"][k] for k in keys]
        kind, axis = constraint["kind"], 0 if constraint["axis"] == "x" else 1
        valid = True
        if kind in {"row", "column", "alignment"}:
            fixed = 1 if kind == "row" else 0 if kind == "column" else axis
            valid = (
                max(p["xy"][fixed] for p in positions)
                - min(p["xy"][fixed] for p in positions)
                <= EPS
            )
        elif kind == "mirror":
            fixed = 1 - axis
            valid = abs(positions[0]["xy"][fixed] - positions[1]["xy"][fixed]) <= EPS
            for vector in ([1, 0], [0, 1]):
                before = transform(vector, positions[0]["orientation"])
                before[axis] *= -1
                after = transform(vector, positions[1]["orientation"])
                valid = valid and all(abs(before[i] - after[i]) <= EPS for i in (0, 1))
        else:
            boxes = sorted((placed[k]["bbox"] for k in keys), key=lambda b: b[0][axis])
            gap = (
                {"compact": 1, "normal": 4, "wide": 8}[constraint["spacing_class"]]
                * layout["grid"]
            )
            valid = all(b[0][axis] - a[1][axis] >= gap - EPS for a, b in zip(boxes, boxes[1:]))
        if not valid:
            issues.append({"code": "template_placement_constraint_failed", "kind": kind,
                           "instances": keys, "axis": constraint["axis"]})
    return issues
