"""Deterministic orthogonal end-to-end wiring for placed schematic instances.

The router is intentionally small and predictable: nets are processed in a
stable order, every candidate path is a grid-aligned polyline with at most
``max_bends`` corners, and the first collision-free candidate wins. A terminal
that cannot reach the net's existing trunk keeps its labelled stub and the
fallback reason is recorded, so the caller can explain the result. Identical
inputs always produce identical wires.

This module never touches OA; it plans geometry for the caller to render.
"""

from __future__ import annotations

import math

from .circuit_geometry_schema import EPS, on_grid

MAX_TERMINALS_PER_NET = 64


def _round_grid(value, grid):
    return round(value / grid) * grid


def _same(point, other):
    return abs(point[0] - other[0]) <= EPS and abs(point[1] - other[1]) <= EPS


def _dedupe(points):
    result = []
    for point in points:
        if not result or not _same(result[-1], point):
            result.append([point[0], point[1]])
    return result


def segments(points):
    return list(zip(points, points[1:]))


def segment_hits_box(a, b, box, clearance):
    """True when an axis-aligned segment comes closer than ``clearance`` to a box.

    Distances (not interval overlaps) keep every degenerate case correct:
    collinear runs, a run exactly on the clearance boundary, and a point-sized
    keep-out box. Being exactly at the clearance distance stays legal.
    """
    low = [min(a[axis], b[axis]) for axis in (0, 1)]
    high = [max(a[axis], b[axis]) for axis in (0, 1)]
    dx = max(box[0][0] - high[0], low[0] - box[1][0], 0)
    dy = max(box[0][1] - high[1], low[1] - box[1][1], 0)
    return math.hypot(dx, dy) < clearance - EPS


def segments_cross(a, b, c, d, clearance):
    """Two axis-aligned segments within ``clearance`` of each other."""
    box = [
        [min(c[0], d[0]), min(c[1], d[1])],
        [max(c[0], d[0]), max(c[1], d[1])],
    ]
    return segment_hits_box(a, b, box, clearance)


def orthogonal_candidates(start, end, max_bends, grid, guides=None):
    """Candidate polylines from ``start`` to ``end`` in a fixed preference order."""
    if _same(start, end):
        return []
    candidates = []
    if abs(start[0] - end[0]) <= EPS or abs(start[1] - end[1]) <= EPS:
        candidates.append([start, end])
    candidates.append([start, [start[0], end[1]], end])
    candidates.append([start, [end[0], start[1]], end])
    if max_bends >= 3:
        guides = guides or {}
        mids_x = [_round_grid((start[0] + end[0]) / 2, grid), *guides.get("x", [])]
        mids_y = [_round_grid((start[1] + end[1]) / 2, grid), *guides.get("y", [])]
        for mid_x in mids_x:
            candidates.append([start, [mid_x, start[1]], [mid_x, end[1]], end])
        for mid_y in mids_y:
            candidates.append([start, [start[0], mid_y], [end[0], mid_y], end])
    result, seen = [], set()
    for candidate in candidates:
        candidate = _dedupe(candidate)
        if len(candidate) < 2 or len(candidate) - 1 > max_bends:
            continue
        if not all(on_grid(point, grid) for point in candidate):
            continue
        key = tuple((point[0], point[1]) for point in candidate)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _guides(start, end, boxes, clearance, grid, limit=4):
    """Free guide lines: channel centers plus just-outside-the-obstacle lines."""
    corridor = [
        [min(start[0], end[0]), min(start[1], end[1])],
        [max(start[0], end[0]), max(start[1], end[1])],
    ]
    xs, ys = set(), set()
    for value in _channel_centers(boxes, clearance, grid, axis=0):
        xs.add(value)
    for value in _channel_centers(boxes, clearance, grid, axis=1):
        ys.add(value)
    for box in boxes.values():
        intersects = not (
            box[1][0] + clearance < corridor[0][0] - EPS
            or box[0][0] - clearance > corridor[1][0] + EPS
            or box[1][1] + clearance < corridor[0][1] - EPS
            or box[0][1] - clearance > corridor[1][1] + EPS
        )
        if not intersects:
            continue
        xs.add(_round_grid(box[1][0] + clearance + grid, grid))
        xs.add(_round_grid(box[0][0] - clearance - grid, grid))
        ys.add(_round_grid(box[1][1] + clearance + grid, grid))
        ys.add(_round_grid(box[0][1] - clearance - grid, grid))
    center_x = (start[0] + end[0]) / 2
    center_y = (start[1] + end[1]) / 2
    return {
        "x": sorted(xs, key=lambda value: (abs(value - center_x), value))[:limit],
        "y": sorted(ys, key=lambda value: (abs(value - center_y), value))[:limit],
    }


def _channel_centers(boxes, clearance, grid, axis, limit=6):
    """Grid-aligned centers of the empty gaps between occupied boxes."""
    intervals = sorted(
        (box[0][axis] - clearance, box[1][axis] + clearance) for box in boxes.values()
    )
    merged = []
    for low, high in intervals:
        if merged and low <= merged[-1][1] + EPS:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    centers = []
    for (_, high), (low, _) in zip(merged, merged[1:]):
        if low - high > grid:
            centers.append(_round_grid((low + high) / 2, grid))
    return centers[:limit]


def _blocked(points, boxes, clearance, first_allowed, last_allowed):
    for index, (a, b) in enumerate(segments(points)):
        allowed = set()
        if index == 0:
            allowed |= first_allowed
        if index == len(points) - 2:
            allowed |= last_allowed
        for owner, box in boxes.items():
            if owner in allowed:
                continue
            if segment_hits_box(a, b, box, clearance):
                return True
    return False


def _crosses_other_nets(points, net, placed, clearance):
    for a, b in segments(points):
        for other_net, other_points in placed:
            if other_net == net:
                continue
            for c, d in segments(other_points):
                if segments_cross(a, b, c, d, clearance):
                    return True
    return False


def _attach_candidates(paths, target, grid):
    """Vertices first, then grid-aligned projections onto existing segments."""
    candidates, seen = [], set()
    for path_index, path in enumerate(paths):
        for point in path["points"]:
            key = (round(point[0], 6), round(point[1], 6))
            if key not in seen:
                seen.add(key)
                candidates.append({"path": path_index, "index": None, "point": list(point)})
    for path_index, path in enumerate(paths):
        for index, (a, b) in enumerate(segments(path["points"])):
            if abs(a[0] - b[0]) <= EPS:
                low, high = sorted((a[1], b[1]))
                point = [a[0], _round_grid(target[1], grid)]
                if low + EPS < point[1] < high - EPS:
                    candidates.append({"path": path_index, "index": index, "point": point})
            else:
                low, high = sorted((a[0], b[0]))
                point = [_round_grid(target[0], grid), a[1]]
                if low + EPS < point[0] < high - EPS:
                    candidates.append({"path": path_index, "index": index, "point": point})
    return candidates


def _split(paths, path_index, index, point):
    """Split one path at an interior attach point.

    The label (if any) always anchors the path start, so it stays on the left
    piece; copying it onto the right piece would create a label with no wire
    underneath, which Virtuoso reports as a floating net.
    """
    path = paths[path_index]
    points = path["points"]
    left = points[: index + 1] + [list(point)]
    right = [list(point)] + points[index + 1 :]
    paths[path_index] = {**path, "points": left}
    paths.append({**path, "points": right, "label_xy": None})
    return path_index


def route_nets(
    terminals,
    boxes,
    grid,
    clearance,
    max_bends=3,
    stub_length=0.25,
    pins=(),
    reserved=(),
    max_detour=2.5,
):
    """Route every net; return wires, junctions and explicit fallbacks.

    ``terminals``: iterable of dicts with ``endpoint``, ``net``, ``anchor``,
    ``escape`` (unit vector), ``owner`` (instance id or None) and ``anchor_id``.
    ``boxes``: mapping of instance id to occupied bbox.
    ``pins``: iterable of ``{"xy": [x, y], "net": net}`` for foreign pin keep-outs.
    ``reserved``: iterable of ``{"net": net, "points": [...]}`` wires that stay
    as drawn (stubs of nets this pass does not route, pin links, ...).
    ``max_detour``: reject a candidate whose drawn length exceeds this multiple of
    the direct Manhattan distance; the terminal then keeps its labelled stub.
    """
    by_net = {}
    for terminal in terminals:
        if terminal.get("net"):
            by_net.setdefault(terminal["net"], []).append(terminal)
    wires, fallbacks, placed = [], [], [
        (wire["net"], wire["points"]) for wire in reserved if wire.get("points")
    ]
    routed_terminals = 0
    for net in sorted(by_net):
        keepout = {
            # Half-clearance around a foreign pin: keeps wires off the pin while
            # still allowing the tight passes engineers draw between pins.
            "pin" + str(index): [
                [pin["xy"][0], pin["xy"][1]],
                [pin["xy"][0], pin["xy"][1]],
            ]
            for index, pin in enumerate(pins)
            if pin.get("net") and pin["net"] != net
        }
        obstacles = {**boxes, **keepout}
        members = by_net[net][:MAX_TERMINALS_PER_NET]
        center = [
            sum(item["anchor"][0] for item in members) / len(members),
            sum(item["anchor"][1] for item in members) / len(members),
        ]
        # Start the trunk at the terminal nearest the group center and then work
        # outwards: compact trees avoid long cross-sheet runs that block peers.
        group = sorted(
            members,
            key=lambda item: (
                math.dist(item["anchor"], center),
                item["anchor"][0],
                item["anchor"][1],
                str(item["endpoint"].get("instance", ""))
                + str(item["endpoint"].get("port", "")),
            ),
        )
        paths, first = [], True
        for terminal in group:
            anchor = [terminal["anchor"][0], terminal["anchor"][1]]
            escape = terminal["escape"]
            start = [
                anchor[0] + escape[0] * stub_length,
                anchor[1] + escape[1] * stub_length,
            ]
            owner = terminal.get("owner")
            owners = {owner} if owner else set()
            if not on_grid(anchor, grid) or not on_grid(start, grid):
                fallbacks.append({"endpoint": terminal["endpoint"], "reason": "off_grid_terminal"})
                continue
            if first:
                paths.append(
                    {"points": [anchor, start], "owners": owners, "label_xy": start,
                     "endpoint": terminal["endpoint"]}
                )
                routed_terminals += 1
                first = False
                continue
            routed = None
            for candidate in sorted(
                _attach_candidates(paths, start, grid),
                key=lambda item: (
                    math.dist(item["point"], start),
                    item["point"][0],
                    item["point"][1],
                ),
            ):
                target_path = paths[candidate["path"]]
                paths_tried = []
                for level in (0, 1, 2):
                    guides = _guides(
                        start,
                        candidate["point"],
                        obstacles,
                        clearance + level * grid,
                        grid,
                    )
                    paths_tried.extend(
                        orthogonal_candidates(
                            start, candidate["point"], max_bends, grid, guides
                        )
                    )
                for path_points in paths_tried:
                    full = _dedupe([anchor] + path_points)
                    # A candidate may leave the anchor along its escape then
                    # immediately retrace that segment. Native schCreateWire
                    # merges the duplicate run and can return nil. Such a
                    # self-overlapping candidate is not a valid drawn route.
                    if any(sum((b[k] - a[k]) * (c[k] - b[k]) for k in (0, 1)) < -EPS
                           for a, b, c in zip(full, full[1:], full[2:])):
                        continue
                    drawn = sum(
                        abs(b[0] - a[0]) + abs(b[1] - a[1])
                        for a, b in segments(full)
                    )
                    direct = abs(start[0] - candidate["point"][0]) + abs(
                        start[1] - candidate["point"][1]
                    )
                    if drawn > max_detour * max(direct, grid):
                        continue
                    if _blocked(
                        full,
                        obstacles,
                        clearance,
                        first_allowed=owners,
                        last_allowed=target_path["owners"],
                    ):
                        continue
                    if _crosses_other_nets(full, net, placed, clearance):
                        continue
                    if candidate["index"] is not None:
                        _split(paths, candidate["path"], candidate["index"], candidate["point"])
                    entry = {
                        "points": full,
                        "owners": owners | target_path["owners"],
                        "label_xy": None,
                        "endpoint": terminal["endpoint"],
                    }
                    paths.append(entry)
                    routed = entry
                    routed_terminals += 1
                    break
                if routed:
                    break
            if routed is None:
                fallbacks.append({"endpoint": terminal["endpoint"], "reason": "no_clear_path"})
                # The labelled stub stays as drawn: later nets must route around it.
                placed.append(
                    (
                        net,
                        [
                            anchor,
                            [
                                anchor[0] + escape[0] * stub_length,
                                anchor[1] + escape[1] * stub_length,
                            ],
                        ],
                    )
                )
        for path in paths:
            wires.append(
                {
                    "endpoint": path["endpoint"],
                    "net": net,
                    "points": path["points"],
                    "label": (
                        {"text": net, "xy": path["label_xy"]}
                        if path["label_xy"] is not None
                        else None
                    ),
                    "kind": "route",
                    "owners": sorted(owner for owner in path["owners"] if owner),
                }
            )
            placed.append((net, path["points"]))
    return {
        "wires": wires,
        "junctions": junction_points(wires),
        "fallbacks": fallbacks,
        "routed_terminals": routed_terminals,
    }


def junction_points(wires):
    """Points where at least three wire segments meet (same net by construction)."""
    degree = {}
    for wire in wires:
        for a, b in segments(wire.get("points") or []):
            for point in (a, b):
                key = (round(point[0], 6), round(point[1], 6))
                degree[key] = degree.get(key, 0) + 1
    return [
        {"xy": [key[0], key[1]], "degree": value}
        for key, value in sorted(degree.items())
        if value >= 3
    ]


def routing_metrics(routed_terminals, fallbacks, wires):
    """Acceptance metrics for one planned routing result (no OA access)."""
    terminals = routed_terminals + len(fallbacks)
    bends = sum(
        max(0, len(wire.get("points", [])) - 2)
        for wire in wires
        if wire.get("kind") in {"route", "branch"}
    )
    return {
        "terminals": terminals,
        "routed_terminals": routed_terminals,
        "fallback_wires": len(fallbacks),
        "end_to_end_ratio": round(routed_terminals / terminals, 4) if terminals else 1.0,
        "labelled_wires": sum(1 for wire in wires if wire.get("label")),
        "bends": bends,
        "junctions": len(junction_points(wires)),
    }
