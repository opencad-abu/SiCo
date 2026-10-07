"""Reserve label space outside master text proxies without changing routing policy."""

import math

from .circuit_geometry_schema import EPS, transform_box


def reserve_stub_labels(wires, sources, layout):
    """Extend only labelled per-pin stubs; the caller checks all resulting geometry.

    Master annotation bounds are unexpanded text proxies, never rendered CDF
    evidence. They keep a fallback label outside its owner's text area. A longer
    stub that touches another net is rejected by the normal geometry checks;
    it is never silently shortened or converted into a routed connection.
    """
    result = []
    grid = layout["grid"]
    for wire in wires:
        owner = wire["endpoint"].get("instance")
        box = sources.get(owner, {}).get("annotation_bbox")
        if (box is None or not wire.get("label") or wire.get("kind")
                or wire.get("end_endpoint") or len(wire["points"]) != 2):
            result.append(wire)
            continue
        pos = layout["instances"][owner]
        box = transform_box(box, pos["orientation"], pos["xy"])
        a, b = wire["points"]
        axis = 0 if abs(b[0] - a[0]) > EPS else 1
        sign = 1 if b[axis] > a[axis] else -1
        boundary = box[1 if sign > 0 else 0][axis]
        length = max(abs(b[axis] - a[axis]), (boundary - a[axis]) * sign + 2 * grid)
        length = math.ceil((length - EPS) / grid) * grid
        end = list(a)
        end[axis] += sign * length
        result.append({**wire, "points": [a, end],
                       "label": {**wire["label"], "xy": end}})
    return result
