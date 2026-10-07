"""Bounded branch rerouting with terminals and true junctions held fixed."""

from itertools import product

from .circuit_geometry_schema import EPS, bounds, overlap
from .circuit_wiring import orthogonal_candidates
from .template_route_detour import MAX_OBSTACLES, MAX_SEGMENTS, _guides
from .template_route_paths import graph_preserved, obstacle_gaps, path_gaps, wire_paths

MAX_BRANCHES = 4
MAX_SCANS = 4096
MAX_CHOICES = 24
MAX_COMBINATIONS = 128


def branches(edges, members):
    """Partition edges at terminals and degree != 2; only unpinned bends vanish."""
    graph = {}
    for i, edge in enumerate(edges):
        a, b = (tuple(p) for p in edge["points"])
        graph.setdefault(a, []).append((i, b))
        graph.setdefault(b, []).append((i, a))
    fixed = {tuple(m["points"][0]) for m in members}
    fixed |= {v for v, neighbors in graph.items() if len(neighbors) != 2}
    used, result = set(), []
    for start in sorted(fixed):
        for index, point in sorted(graph.get(start, []), key=lambda row: row[1]):
            if index in used:
                continue
            points, indices = [start], []
            while index not in used:
                used.add(index)
                indices.append(index)
                points.append(point)
                if point in fixed:
                    break
                index, point = next((i, p) for i, p in graph[point] if i != index)
            result.append((indices, [list(p) for p in points]))
    return result if len(used) == len(edges) else []


def _length(points):
    return sum(abs(b[0]-a[0]) + abs(b[1]-a[1]) for a, b in zip(points, points[1:]))


def _candidates(start, end, grid, guides, max_bends):
    # The legacy helper takes a segment budget; this stage uses the public
    # option's actual corner count, capped at three corners / four segments.
    rows = orthogonal_candidates(start, end, min(max_bends + 1, 3), grid, guides)
    if max_bends >= 3:
        for x, y in product(guides["x"], guides["y"]):
            rows.extend([[start, [x, start[1]], [x, y], [end[0], y], end],
                         [start, [start[0], y], [x, y], [x, end[1]], end]])
    result = set()
    for row in rows:
        points = []
        for p in row:
            p = tuple(p)
            if not points or p != points[-1]:
                points.append(p)
        # Reject retracing and extra collinear vertices; simpler candidates
        # already cover useful straight segments and one/two-corner paths.
        if any((a[0] == b[0] == c[0]) or (a[1] == b[1] == c[1])
               for a, b, c in zip(points, points[1:], points[2:])):
            continue
        if 2 <= len(points) <= max_bends + 2:
            result.add(tuple(points))
    return [[list(p) for p in row] for row in sorted(result, key=lambda r: (_length(r), len(r), r))]


def _parts(edges, indices, points):
    sources = {tuple((s["shape"], s["segment"])) for i in indices
               for s in edges[i].get("chain", [edges[i]])}
    chain = [dict(shape=s, segment=t) for s, t in sorted(sources)]
    return [dict(chain[0], chain=chain, points=[a, b]) for a, b in zip(points, points[1:])]


def chain_paths(edges, members, layout, placed, pins, reserved, diagnostics):
    """Replace at most four whole branches, committing only a valid full graph."""
    scans, combinations, rejected = 0, 0, set()

    def failed(reason):
        diagnostics.append(dict(code="target_branch_chain_exhausted", scans=scans,
                                candidates=combinations, reasons=[reason],
                                candidate_rejections=sorted(rejected)))
        return None

    net, grid = members[0]["net"], layout["grid"]
    boxes = [i["bbox"] for i in placed.values()]
    boxes += [[p["xy"], p["xy"]] for p in pins if p["net"] != net]
    if len(edges) > MAX_SEGMENTS or len(boxes) > MAX_OBSTACLES:
        return failed("branch_chain_work_limit")
    foreign = []
    for wire in reserved:
        if wire["net"] != net:
            for a, b in zip(wire["points"], wire["points"][1:]):
                if len(boxes) >= MAX_OBSTACLES:
                    return failed("branch_chain_work_limit")
                foreign.append(bounds([a, b]))
                boxes.append(foreign[-1])
    vertices = {tuple(p) for e in edges for p in e["points"]}
    anchors = [dict(xy=m["points"][0], endpoint=m["endpoint"]) for m in members]
    if (not graph_preserved(edges, anchors, len(vertices), grid)
            or path_gaps(wire_paths(edges, members, net), members, layout, placed, pins)):
        return failed("branch_chain_unverified_target_graph")
    groups = branches(edges, members)
    colliding = [(ids, points) for ids, points in groups if any(
        overlap(bounds(edges[i]["points"]), box, layout.get("clearance", 0))
        for i in ids for box in foreign)]
    if not colliding or len(colliding) > MAX_BRANCHES:
        return failed("branch_chain_count_limit")
    # A neighboring branch at a pinned junction may occupy the only legal
    # departure direction. Include its bend freedom in the atomic search.
    contacts = {tuple(p) for _, ps in colliding for p in (ps[0], ps[-1])}
    adjacent = [(ids, ps) for ids, ps in groups if (ids, ps) not in colliding
                and contacts & {tuple(ps[0]), tuple(ps[-1])}]
    if len(colliding) + len(adjacent) > MAX_BRANCHES:
        return failed("branch_chain_count_limit")
    colliding += adjacent
    colliding.sort(key=lambda row: (row[1][0], row[1][-1]))
    removed = {i for ids, _ in colliding for i in ids}
    unchanged = [e for i, e in enumerate(edges) if i not in removed]
    if len(unchanged) + 4 * len(colliding) > MAX_SEGMENTS:
        return failed("branch_chain_work_limit")
    routing = layout.get("routing", {})
    if MAX_CHOICES <= 0:
        return failed("branch_chain_choice_limit")
    choices = []
    for ids, points in colliding:
        guides = _guides(boxes, points[0], points[-1], grid, layout.get("clearance", 0))
        candidates = [points] + _candidates(points[0], points[-1], grid, guides,
                                            min(routing.get("max_bends", 3), 3))
        legal = []
        for candidate in candidates:
            if scans >= MAX_SCANS:
                return failed("branch_chain_scan_limit")
            scans += 1
            if _length(candidate) > _length(points) * routing.get("max_detour", 2.5) + EPS:
                rejected.add("branch_chain_length_limit")
                continue
            parts = _parts(edges, ids, candidate)
            gaps = obstacle_gaps(wire_paths(parts, members, net), members, layout,
                                 placed, pins, reserved)
            if gaps:
                rejected.update(gaps)
                continue
            legal.append(parts)
            if len(legal) >= MAX_CHOICES:
                break
        if not legal:
            return failed("branch_chain_no_legal_candidate")
        choices.append(legal)
    endpoints = {tuple(p) for _, points in colliding for p in (points[0], points[-1])}
    fixed = {tuple(p) for e in unchanged for p in e["points"]} | endpoints
    for selected in product(*choices):
        if combinations >= MAX_COMBINATIONS:
            return failed("branch_chain_candidate_limit")
        combinations += 1
        trial = unchanged + [part for parts in selected for part in parts]
        count = len(fixed) + sum(len(parts)-1 for parts in selected)
        if not graph_preserved(trial, anchors, count, grid):
            rejected.add("target_graph_not_preserved")
            continue
        paths = wire_paths(trial, members, net)
        gaps = path_gaps(paths, members, layout, placed, pins, reserved)
        if gaps:
            rejected.update(gaps)
            continue
        diagnostics.append(dict(code="target_branch_chain_applied", scans=scans,
                                candidates=combinations, branches=len(colliding)))
        return paths
    return failed("branch_chain_no_legal_combination")
