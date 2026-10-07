"""Axis intersections with static symbol outlines, independent of cell/PDK identity."""

import math

from .template_schema import TemplateUnavailable

GRID = 0.0625
MAX_STUB = 5 * GRID
EPS = 1e-8
SIDES = {"left": (0, -1), "right": (0, 1), "bottom": (1, -1), "top": (1, 1)}


def segments(kind, points):
    if kind == "rectangle":
        lo, hi = points
        points = [lo, [hi[0], lo[1]], hi, [lo[0], hi[1]]]
        kind = "polygon"
    if kind in {"line", "polygon"}:
        return list(zip(points, points[1:] + (points[:1] if kind == "polygon" else [])))
    return []


def in_sweep(angle, start, stop):
    tau = 2 * math.pi
    if abs(stop - start) >= tau - EPS:
        return True
    offset = (angle - start) % tau
    return offset <= (stop - start) % tau + EPS or tau - offset <= EPS


def intersections(body, axis, lane):
    """All real intersections with a horizontal (axis=0) or vertical line."""
    other = 1 - axis
    result = []
    for kind, points, extra in body:
        for first, last in segments(kind, points):
            delta = last[other] - first[other]
            if abs(delta) <= EPS:
                if abs(lane - first[other]) <= EPS:
                    result.extend([first[:], last[:]])
                continue
            fraction = (lane - first[other]) / delta
            if -EPS <= fraction <= 1 + EPS:
                point = [first[i] + fraction * (last[i] - first[i]) for i in (0, 1)]
                point[other] = lane
                result.append(point)
        if kind not in {"ellipse", "arc"}:
            continue
        box = points if kind == "ellipse" else extra[0]
        center = [(box[0][i] + box[1][i]) / 2 for i in (0, 1)]
        radius = [(box[1][i] - box[0][i]) / 2 for i in (0, 1)]
        if min(radius) <= EPS:
            raise TemplateUnavailable("degenerate template ellipse/arc")
        ratio = (lane - center[other]) / radius[other]
        if abs(ratio) > 1 + EPS:
            continue  # No bbox fallback and no clamping an out-of-range ray.
        reach = radius[axis] * math.sqrt(max(0.0, 1 - ratio * ratio))
        for sign in (-1, 1):
            point = center[:]
            point[axis] += sign * reach
            point[other] = lane
            angle = math.atan2(
                (point[1] - center[1]) / radius[1], (point[0] - center[0]) / radius[0]
            )
            if kind == "ellipse" or in_sweep(angle, extra[1], extra[2]):
                result.append(point)
    return result


def side_of(anchor, box):
    for side, (axis, sign) in SIDES.items():
        if sign * (anchor[axis] - box[int(sign > 0)][axis]) > EPS:
            return side
    return min(
        SIDES, key=lambda s: abs(anchor[SIDES[s][0]] - box[int(SIDES[s][1] > 0)][SIDES[s][0]])
    )


def attach_stubs(body, box, anchors, sides):
    """Put grid pins outside the body and join them to its actual contour.

    All pins on a side share an outer grid line, preserving the existing
    occupied-bbox escape contract for generic TB consumers. Search alternate
    grid lanes when a sloped/curved body is too far from the preferred lane.
    A half-grid length reserve accommodates target DBU rounding; native code
    enforces the full five-grid cap on the actual saved line.
    """
    result, used = [], set()
    for name, anchor in anchors.items():
        side = sides[name]
        axis, sign = SIDES[side]
        other = 1 - axis
        edge = box[int(sign > 0)][axis]
        outer = (
            math.ceil(edge / GRID - EPS) + 1 if sign > 0 else math.floor(edge / GRID + EPS) - 1
        ) * GRID
        lanes = range(
            math.ceil(box[0][other] / GRID - EPS), math.floor(box[1][other] / GRID + EPS) + 1
        )
        lanes = sorted(lanes, key=lambda n: (abs(n * GRID - anchor[other]), n))
        for lane_index in lanes:
            lane = lane_index * GRID
            pin = [0.0, 0.0]
            pin[axis], pin[other] = outer, lane
            if tuple(pin) in used:
                continue
            hits = intersections(body, axis, lane)
            if not hits:
                continue
            contact = max(hits, key=lambda p: sign * p[axis])
            length = sign * (outer - contact[axis])
            if EPS < length <= MAX_STUB - GRID / 2 + EPS:
                anchors[name] = pin
                used.add(tuple(pin))
                result.append(["line", [pin, contact], None])
                break
        else:
            raise TemplateUnavailable(
                "no distinct grid pin with a body contact within five grid steps: " + name
            )
    return result
