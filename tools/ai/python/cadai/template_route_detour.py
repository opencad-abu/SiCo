"""Bounded multi-edge detours of already verified target template graphs."""

import math

from .circuit_geometry_schema import EPS, bounds, overlap
from .circuit_wiring import orthogonal_candidates
from .template_route_paths import graph_preserved, path_gaps, wire_paths

MAX_CANDIDATES = 128
MAX_GUIDES_PER_AXIS = 24
MAX_OBSTACLES = 4096
MAX_SEGMENTS = 512
MAX_REPAIR_EDGES = 4


def _guides(boxes, start, end, grid, clearance):
    """Snap away from obstacles, then prefer the nearest bounded guide tracks."""
    guides = {}
    for axis, name in enumerate("xy"):
        values = set()
        for box in boxes:
            values.add((math.floor((box[0][axis] - clearance) / grid) - 1) * grid)
            values.add((math.ceil((box[1][axis] + clearance) / grid) + 1) * grid)
        center = (start[axis] + end[axis]) / 2
        guides[name] = sorted(values, key=lambda v: (abs(v - center), v))[:MAX_GUIDES_PER_AXIS]
    return guides


def detour_paths(edges, members, layout, placed, pins, reserved, diagnostics):
    """Atomically replace up to four colliding edges, retaining all old vertices.

    Search uses deterministic depth-first backtracking and a single shared work
    budget. Partial repairs never escape. Each subdivided edge keeps its original
    endpoints; the complete graph is recompiled before descending to the next edge.
    """
    net, grid = members[0]["net"], layout["grid"]
    foreign, boxes = [], [i["bbox"] for i in placed.values()]
    boxes += [[p["xy"], p["xy"]] for p in pins if p["net"] != net]
    attempts, rejected = 0, set()

    def failed(reason):
        diagnostics.append(dict(code="target_local_detour_exhausted", candidates=attempts,
                                reasons=[reason], candidate_rejections=sorted(rejected)))
        return None

    if len(boxes) > MAX_OBSTACLES or len(edges) > MAX_SEGMENTS:
        return failed("local_detour_work_limit")
    for wire in reserved:
        if wire["net"] == net:
            continue
        for a, b in zip(wire["points"], wire["points"][1:]):
            if len(boxes) >= MAX_OBSTACLES:
                return failed("local_detour_work_limit")
            box = bounds([a, b])
            foreign.append(box)
            boxes.append(box)
    clearance = layout.get("clearance", 0)
    def collision(edge):
        return any(overlap(bounds(edge["points"]), box, clearance) for box in foreign)

    colliding = [i for i, e in enumerate(edges) if collision(e)]
    if not colliding or len(colliding) > MAX_REPAIR_EDGES:
        return failed("local_detour_edge_limit")
    if len(edges) + 2 * len(colliding) > MAX_SEGMENTS:
        return failed("local_detour_work_limit")
    routing = layout.get("routing", {})
    # Existing orthogonal_candidates interprets max_bends as a segment budget.
    max_segments = min(routing.get("max_bends", 3), 3)
    vertices = len({tuple(p) for e in edges for p in e["points"]})
    anchors = [{"xy": m["points"][0], "endpoint": m["endpoint"]} for m in members]
    initial = wire_paths(edges, members, net)
    if (path_gaps(initial, members, layout, placed, pins)
            or not graph_preserved(edges, anchors, vertices, grid)):
        return failed("local_detour_unverified_target_graph")
    replacements = {}

    def search(depth):
        nonlocal attempts
        index = colliding[depth]
        edge = edges[index]
        start, end = sorted(edge["points"])
        guides = _guides(boxes, start, end, grid, clearance)
        limit = routing.get("max_detour", 2.5) * (abs(end[0]-start[0]) + abs(end[1]-start[1]))
        for candidate in orthogonal_candidates(start, end, max_segments, grid, guides):
            if len(candidate) <= 2:
                continue
            if attempts >= MAX_CANDIDATES:
                return None
            attempts += 1
            length = sum(abs(b[0]-a[0]) + abs(b[1]-a[1])
                         for a, b in zip(candidate, candidate[1:]))
            if length > limit + EPS:
                rejected.add("local_detour_length_limit")
                continue
            branch = [{**edge, "points": [a, b]} for a, b in zip(candidate, candidate[1:])]
            replacements[index] = branch
            trial = [part for i, original in enumerate(edges)
                     for part in replacements.get(i, [original])]
            count = vertices + sum(len(r)-1 for r in replacements.values())
            if not graph_preserved(trial, anchors, count, grid):
                rejected.add("target_graph_not_preserved")
            else:
                paths = wire_paths(trial, members, net)
                gaps = path_gaps(paths, members, layout, placed, pins)
                if gaps:
                    rejected.update(gaps)
                elif any(collision(part) for part in branch):
                    rejected.add("template_paths_different_nets_touch")
                elif depth + 1 < len(colliding):
                    result = search(depth + 1)
                    if result:
                        return result
                elif not path_gaps(paths, members, layout, placed, pins, reserved):
                    return paths
            replacements.pop(index)
        return None

    result = search(0)
    if result:
        repaired = [dict(shape=edges[i]["shape"], segment=edges[i]["segment"],
                         points=edges[i]["points"]) for i in colliding]
        diagnostics.append(dict(code="target_local_detour_applied", candidates=attempts,
                                **({"source_edge": repaired[0]} if len(repaired) == 1 else
                                   {"source_edges": repaired})))
        return result
    return failed("local_detour_candidate_limit" if attempts >= MAX_CANDIDATES else
                  "local_detour_no_legal_candidate")
