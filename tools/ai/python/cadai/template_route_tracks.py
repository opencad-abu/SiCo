"""Bounded translation of connected, terminal-free tracks in verified graphs."""

from .circuit_geometry_schema import EPS, bounds, overlap
from .template_route_detour import MAX_OBSTACLES, MAX_SEGMENTS, _guides
from .template_route_paths import graph_preserved, path_gaps, wire_paths

MAX_CANDIDATES = 128
MAX_TRACKS = 16


def _free_tracks(edges, fixed):
    """A track is a connected collinear component, not every equal coordinate."""
    for axis in (0, 1):
        graph = {}
        for edge in edges:
            a, b = (tuple(p) for p in edge["points"])
            if a[axis] == b[axis]:
                graph.setdefault(a, set()).add(b)
                graph.setdefault(b, set()).add(a)
        unseen = set(graph)
        while unseen:
            pending, component = [min(unseen)], set()
            while pending:
                point = pending.pop()
                if point in component:
                    continue
                component.add(point)
                pending.extend(graph[point] - component)
            unseen -= component
            if not component & fixed:
                yield axis, component


def _translate(edges, component, axis, value, ratio):
    """Keep every edge identity and direction; limit each stretched branch."""
    moved = []
    for edge in edges:
        points = [[value if k == axis and tuple(p) in component else p[k] for k in (0, 1)]
                  for p in edge["points"]]
        old, new = edge["points"], points
        direction = 0 if old[0][1] == old[1][1] else 1
        before = old[1][direction] - old[0][direction]
        after = new[1][direction] - new[0][direction]
        if before * after <= 0 or abs(after) > abs(before) * ratio + EPS:
            return None
        moved.append(dict(edge, points=points))
    return moved


def track_paths(edges, members, layout, placed, pins, reserved, diagnostics):
    """Move one free track atomically, with all terminals and adjacency fixed.

    This fallback deliberately does not nest detour searches or move pinned
    tracks. Complete candidates alone undergo the common graph/obstacle checks.
    """
    net, grid = members[0]["net"], layout["grid"]
    attempts, rejected = 0, set()

    def failed(reason):
        diagnostics.append(dict(code="target_free_track_exhausted", candidates=attempts,
                                reasons=[reason], candidate_rejections=sorted(rejected)))
        return None

    boxes = [i["bbox"] for i in placed.values()]
    boxes += [[p["xy"], p["xy"]] for p in pins if p["net"] != net]
    if len(edges) > MAX_SEGMENTS or len(boxes) > MAX_OBSTACLES:
        return failed("free_track_work_limit")
    foreign = []
    for wire in reserved:
        if wire["net"] == net:
            continue
        for a, b in zip(wire["points"], wire["points"][1:]):
            if len(boxes) >= MAX_OBSTACLES:
                return failed("free_track_work_limit")
            box = bounds([a, b])
            foreign.append(box)
            boxes.append(box)
    anchors = [dict(xy=m["points"][0], endpoint=m["endpoint"]) for m in members]
    fixed = {tuple(a["xy"]) for a in anchors}
    vertices = {tuple(p) for e in edges for p in e["points"]}
    if (not graph_preserved(edges, anchors, len(vertices), grid)
            or path_gaps(wire_paths(edges, members, net), members, layout, placed, pins)):
        return failed("free_track_unverified_target_graph")
    clearance = layout.get("clearance", 0)
    conflicts = {tuple(p) for e in edges if any(
        overlap(bounds(e["points"]), box, clearance) for box in foreign) for p in e["points"]}
    tracks = [(axis, points) for axis, points in _free_tracks(edges, fixed) if points & conflicts]
    tracks.sort(key=lambda row: (-len(row[1] & conflicts), row[0], sorted(row[1])))
    ratio = layout.get("routing", {}).get("max_detour", 2.5)
    for axis, component in tracks[:MAX_TRACKS]:
        point = list(min(component))
        for value in _guides(boxes, point, point, grid, clearance)["xy"[axis]]:
            if abs(value - point[axis]) <= EPS:
                continue
            if attempts >= MAX_CANDIDATES:
                return failed("free_track_candidate_limit")
            attempts += 1
            trial = _translate(edges, component, axis, value, ratio)
            if trial is None:
                rejected.add("free_track_direction_or_length_limit")
                continue
            if not graph_preserved(trial, anchors, len(vertices), grid):
                rejected.add("target_graph_not_preserved")
                continue
            paths = wire_paths(trial, members, net)
            gaps = path_gaps(paths, members, layout, placed, pins, reserved)
            if gaps:
                rejected.update(gaps)
                continue
            diagnostics.append(dict(code="target_free_track_applied", candidates=attempts,
                                    axis="xy"[axis], source=point[axis], target=value,
                                    vertices=len(component)))
            return paths
    return failed("free_track_track_limit" if len(tracks) > MAX_TRACKS else
                  "free_track_no_legal_candidate")
