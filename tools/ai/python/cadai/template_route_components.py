"""Atomic collision-component repair with bounded unpinned junction motion."""

from .circuit_geometry_schema import EPS, bounds, overlap
from .template_route_chains import _candidates, _length, _parts, branches
from .template_route_detour import MAX_OBSTACLES, MAX_SEGMENTS, _guides
from .template_route_motion import motion_states
from .template_route_paths import graph_preserved, obstacle_gaps, path_gaps, wire_paths

MAX_BRANCHES = 16
MAX_COMPONENTS = 8
MAX_STATES = 64
MAX_UNITS = 8
MAX_SCANS = 8192
MAX_CHOICES = 12
MAX_STEPS = 1024


def _components(groups, hits):
    """Close each collision over incident branches, then merge overlapping scopes."""
    ends = [{tuple(ps[0]), tuple(ps[-1])} for _, ps in groups]
    scopes = []
    for i in sorted(hits):
        scope = {j for j, points in enumerate(ends) if points & ends[i]}
        merged = [old for old in scopes if old & scope]
        for old in merged:
            scope |= old
            scopes.remove(old)
        scopes.append(scope)
    return sorted(scopes, key=lambda s: min(s))


def _states(edges, groups, scope, fixed, boxes, grid, clearance, feedback, allowed):
    return motion_states(edges, groups, scope, fixed, boxes, grid, clearance,
                         feedback, max_units=MAX_UNITS, allowed=allowed)


def component_paths(edges, members, layout, placed, pins, reserved, diagnostics):
    """Repair disjoint conflict scopes privately; return only a valid entire net.

    A state combines up to two nonterminal junction/rail coordinate moves and
    reroutes every incident branch. All components/states share work counters.
    There is no backtracking across completed components or neighboring nets.
    """
    net, grid = members[0]["net"], layout["grid"]
    scans, steps, states = 0, 0, 0
    rejected = set()

    def failed(reason):
        diagnostics.append(dict(code="target_conflict_component_exhausted", scans=scans,
                                candidates=steps, states=states, reasons=[reason],
                                candidate_rejections=sorted(rejected)))
        return None

    boxes = [i["bbox"] for i in placed.values()]
    boxes += [[p["xy"], p["xy"]] for p in pins if p["net"] != net]
    if len(edges) > MAX_SEGMENTS or len(boxes) > MAX_OBSTACLES or len(members) > 256:
        return failed("conflict_component_work_limit")
    foreign = []
    for wire in reserved:
        if wire["net"] != net:
            for a, b in zip(wire["points"], wire["points"][1:]):
                if len(boxes) >= MAX_OBSTACLES:
                    return failed("conflict_component_work_limit")
                foreign.append(bounds([a, b]))
                boxes.append(foreign[-1])
    anchors = [dict(xy=m["points"][0], endpoint=m["endpoint"]) for m in members]
    vertices = {tuple(p) for e in edges for p in e["points"]}
    if (not graph_preserved(edges, anchors, len(vertices), grid)
            or path_gaps(wire_paths(edges, members, net), members, layout, placed, pins)):
        return failed("conflict_component_unverified_target_graph")
    groups = branches(edges, members)
    clearance = layout.get("clearance", 0)
    hits = {i for i, (ids, _) in enumerate(groups) if any(
        overlap(bounds(edges[j]["points"]), box, clearance) for j in ids for box in foreign)}
    scopes = _components(groups, hits)
    if (not scopes or len(scopes) > MAX_COMPONENTS
            or any(len(s) > MAX_BRANCHES for s in scopes)):
        return failed("conflict_component_scope_limit")
    if MAX_CHOICES <= 0:
        return failed("conflict_component_choice_limit")
    fixed = {tuple(a["xy"]) for a in anchors}
    endpoints = {tuple(p) for _, ps in groups for p in (ps[0], ps[-1])}
    routing = layout.get("routing", {})
    current = {i: ps for i, (_, ps) in enumerate(groups)}
    motions, motion_units = [], 0
    cached = {}

    def allowed(moved):
        if len({moved.get(p, p) for p in endpoints}) != len(endpoints):
            return False
        probes = [dict(edges[0], points=[list(p), list(p)]) for p in moved.values()]
        return not obstacle_gaps(wire_paths(probes, members, net), members, layout,
                                 placed, pins, reserved)

    def compile_paths(paths):
        trial = [part for i, (ids, _) in enumerate(groups)
                 for part in _parts(edges, ids, paths[i])]
        count = len({tuple(p) for ps in paths.values() for p in (ps[0], ps[-1])})
        if count != len(endpoints):
            rejected.add("target_graph_not_preserved")
            return None
        count += sum(len(ps)-2 for ps in paths.values())
        if len(trial) > MAX_SEGMENTS or not graph_preserved(trial, anchors, count, grid):
            rejected.add("target_graph_not_preserved")
            return None
        return wire_paths(trial, members, net)

    for scope in scopes:
        found = None
        feedback = {}
        state_iter = _states(edges, groups, scope, fixed, boxes, grid, clearance, feedback, allowed)
        for moved in state_iter:
            feedback["blocked"] = None
            if states >= MAX_STATES or scans >= MAX_SCANS or steps >= MAX_STEPS:
                return failed("conflict_component_search_limit")
            states += 1
            if len({moved.get(p, p) for p in endpoints}) != len(endpoints):
                rejected.add("target_graph_not_preserved")
                continue
            choices = {}
            for i in sorted(scope, key=lambda n: (n not in hits, n)):
                ids, original = groups[i]
                start, end = [list(moved.get(tuple(p), tuple(p))) for p in
                              (original[0], original[-1])]
                key = (i, tuple(start), tuple(end))
                if key in cached:
                    if not cached[key]:
                        feedback["blocked"] = (tuple(original[0]), tuple(original[-1]))
                        break
                    choices[i] = cached[key]
                    continue
                # A branch cannot escape an obstructed endpoint by adding
                # bends. Use the common obstacle guard on point probes before
                # spending the scan budget on paths that must all fail.
                if scans >= MAX_SCANS:
                    return failed("conflict_component_search_limit")
                scans += 1
                probes = [dict(edges[ids[0]], points=[p, p]) for p in (start, end)]
                gaps = obstacle_gaps(wire_paths(probes, members, net), members, layout,
                                     placed, pins, reserved)
                if gaps:
                    rejected.update(gaps)
                    cached[key] = []
                    feedback["blocked"] = (tuple(original[0]), tuple(original[-1]))
                    break
                guides = _guides(boxes, start, end, grid, clearance)
                candidates = ([] if start != original[0] or end != original[-1] else [original])
                candidates += _candidates(start, end, grid, guides,
                                           min(routing.get("max_bends", 3), 3))
                legal = []
                for candidate in candidates:
                    if scans >= MAX_SCANS:
                        return failed("conflict_component_search_limit")
                    scans += 1
                    limit = _length(original) * routing.get("max_detour", 2.5)
                    if _length(candidate) > limit + EPS:
                        rejected.add("conflict_component_length_limit")
                        # The unchanged branch fits max_detour >= 1; generated
                        # candidates are sorted by length. All remaining paths
                        # also exceed the limit, so do not scan them repeatedly.
                        break
                    gaps = obstacle_gaps(wire_paths(_parts(edges, ids, candidate), members, net),
                                         members, layout, placed, pins, reserved)
                    if gaps:
                        rejected.update(gaps)
                        continue
                    legal.append(candidate)
                    if len(legal) >= MAX_CHOICES:
                        break
                cached[key] = legal
                if not legal:
                    feedback["blocked"] = (tuple(original[0]), tuple(original[-1]))
                    break
                choices[i] = legal
            if len(choices) != len(scope):
                continue
            order = sorted(scope, key=lambda i: (len(choices[i]), i))
            trial = {i: ps for i, ps in current.items() if i not in scope}

            def search(depth):
                nonlocal steps
                if depth == len(order):
                    result = compile_paths(trial)
                    return dict(trial) if result else None
                i = order[depth]
                for candidate in choices[i]:
                    if steps >= MAX_STEPS:
                        return None
                    steps += 1
                    trial[i] = candidate
                    result = search(depth + 1)
                    if result:
                        return result
                    trial.pop(i)
                return None

            found = search(0)
            if found:
                current = found
                motion_units = max(motion_units, feedback.get("depth", 0))
                motions.extend(dict(source=list(p), target=list(q))
                               for p, q in sorted(moved.items()))
                break
        if not found:
            return failed("conflict_component_search_limit" if steps >= MAX_STEPS else
                          "conflict_component_motion_limit" if feedback.get("truncated") else
                          "conflict_component_no_legal_candidate")
    result = compile_paths(current)
    gaps = path_gaps(result, members, layout, placed, pins, reserved) if result else []
    if not result or gaps:
        rejected.update(gaps)
        return failed("conflict_component_final_check")
    diagnostics.append(dict(code="target_conflict_component_applied", scans=scans,
                            candidates=steps, states=states, components=len(scopes),
                            branches=sum(len(s) for s in scopes), junction_moves=motions,
                            motion_units=motion_units))
    return result
