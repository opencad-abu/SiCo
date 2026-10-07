"""Pinned coordinate maps with global or verified edge-local order constraints."""

import bisect

from .circuit_geometry_schema import EPS, on_grid

MAX_LOCAL_EDGES = 512
MAX_LOCAL_ANCHORS = 256


def axis_map(points, constraints, grid, dbu, diagnostics=None, axis=None, *, ordered=True):
    """Pinned tracks remain exact; free tracks interpolate or retain outside pitch."""
    pins = {}
    for old, new in constraints:
        if old in pins and abs(pins[old] - new) > EPS:
            if diagnostics is not None:
                diagnostics.append({"code": "axis_track_conflict", "axis": axis,
                                    "source": old, "targets": [pins[old], new]})
            return None
        if not on_grid([new], grid):
            if diagnostics is not None:
                diagnostics.append({"code": "target_anchor_off_grid", "axis": axis,
                                    "target": new, "grid": grid})
            return None
        pins[old] = new
    levels = sorted(pins)
    if not levels:
        if diagnostics is not None:
            diagnostics.append({"code": "source_track_unavailable", "axis": axis})
        return None
    if ordered and any(pins[a] > pins[b] + EPS for a, b in zip(levels, levels[1:])):
        if diagnostics is not None:
            diagnostics.append({"code": "axis_track_order_reversed", "axis": axis,
                                "tracks": [
                                    [a, pins[a], b, pins[b]]
                                    for a, b in zip(levels, levels[1:])
                                    if pins[a] > pins[b] + EPS
                                ]})
        return None
    result = {}
    for point in sorted(points):
        if point in pins:
            result[point] = pins[point]
            continue
        index = bisect.bisect_left(levels, point)
        if index in (0, len(levels)):
            near = levels[0] if index == 0 else levels[-1]
            value = pins[near] + (point - near) / dbu
        else:
            a, b = levels[index - 1], levels[index]
            value = pins[a] + (point - a) * (pins[b] - pins[a]) / (b - a)
        result[point] = round(value / grid) * grid
    return result


def local_order_maps(edges, anchors, grid, dbu, diagnostics):
    """Permit unrelated tracks to exchange order, never reverse an actual edge.

    This produces one candidate only. The caller must still prove vertex
    injectivity, full adjacency, shared contacts, grid and target obstacles.
    No rail conflict, missing evidence or off-grid anchor is relaxed.
    """
    if len(edges) > MAX_LOCAL_EDGES or len(anchors) > MAX_LOCAL_ANCHORS:
        diagnostics.append(dict(code="local_order_work_limit"))
        return None
    maps = [axis_map({p[k] for e in edges for p in e["points"]},
                     [(a[k], b[k]) for a, b in anchors], grid, dbu, diagnostics,
                     axis="xy"[k], ordered=False) for k in (0, 1)]
    if any(m is None for m in maps):
        return None
    for edge in edges:
        a, b = edge["points"]
        axis = 0 if a[1] == b[1] else 1
        before = b[axis] - a[axis]
        after = maps[axis][b[axis]] - maps[axis][a[axis]]
        if before * after <= 0:
            diagnostics.append(dict(code="local_order_edge_reversed_or_collapsed",
                                    axis="xy"[axis], source_edge=edge))
            return None
    return maps
