"""Bounded conflict search over complete, source-proved neighboring net graphs."""

from heapq import heappop, heappush

from .circuit_spec_schema import digest
from .template_route_conflicts import conflict_groups, select_conflict
from .template_route_paths import graph_preserved, path_gaps
from .template_route_repair import repair_paths

MAX_NETS = 4
MAX_NODES = 24
MAX_REPLANS = 32
MAX_CONSTRAINTS = 16
MAX_SEGMENTS = 512
MAX_TOTAL_SEGMENTS = 2048
MAX_OBSTACLES = 4096
MAX_CHOICES = 4
MAX_QUEUED = 64
MAX_TRANSITIONS = 128
MAX_VALIDATIONS = 1024
MAX_FALLBACK_REPLANS = 4


def _signature(paths):
    return tuple(sorted(tuple(sorted(tuple(p) for p in w["points"])) for w in paths))


def network_paths(original, members, layout, placed, pins, outside, diagnostics):
    """Branch on either side of a collision and backtrack whole net alternatives.

    Each child forbids one opposing segment and reroutes that net from its
    original proved graph, retaining all prior constraints. Other changing nets
    are provisional; their real terminals stay obstacles throughout. Only a
    complete, mutually valid set may return. This is a conservative bounded
    search, not a complete multi-agent pathfinding solver.
    """
    expanded, replans, rejected = 0, 0, set()
    local_failures, last_conflict = set(), None
    visits, net_replans = {}, {}
    reused, transitions, witness_trials, stalled_pairs = 0, 0, 0, 0
    checks = {}
    choice_branches, fallback_replans = 0, 0

    def coverage():
        return dict(conflict_pairs=[dict(nets=list(pair), selections=count)
                                    for pair, count in sorted(visits.items())],
                    net_replans=dict(sorted(net_replans.items())),
                    candidate_reuses=reused, transitions=transitions,
                    witness_trials=witness_trials, stalled_pairs=stalled_pairs,
                    candidate_checks=len(checks), choice_branches=choice_branches,
                    fallback_replans=fallback_replans)

    def failed(reason):
        diagnostics.append(dict(code="target_network_search_exhausted", nodes=expanded,
                                replans=replans, reasons=[reason],
                                candidate_rejections=sorted(rejected),
                                local_failure_reasons=sorted(local_failures),
                                last_conflict=last_conflict, **coverage()))
        return None

    if (not original or len(original) > MAX_NETS
            or any(not ws or len(ws) > MAX_SEGMENTS for ws in original.values())
            or sum(map(len, original.values())) > MAX_TOTAL_SEGMENTS
            or any(not members.get(n) or len(members[n]) > 256 for n in original)):
        return failed("network_search_work_limit")
    anchors, edges = {}, {}
    # Retain all terminals even if a participating net's old wires are replaced.
    guards = list(pins) + [dict(net=n, xy=m["points"][0])
                          for n in sorted(original) for m in members[n]]
    outside_count = sum(max(0, len(w["points"]) - 1) for w in outside)
    if len(guards) + len(placed) + outside_count > MAX_OBSTACLES:
        return failed("network_search_work_limit")
    for net, paths in original.items():
        if (not members.get(net) or len(members[net]) > 256
                or not all(w.get("template_segment") and w.get("template_source")
                           and len(w["points"]) == 2 and w["net"] == net for w in paths)):
            return failed("network_search_unverified_graph")
        edges[net] = [dict(w["template_source"], points=w["points"]) for w in paths]
        anchors[net] = [dict(xy=m["points"][0], endpoint=m["endpoint"]) for m in members[net]]
        count = len({tuple(p) for w in paths for p in w["points"]})
        if (not graph_preserved(edges[net], anchors[net], count, layout["grid"])
                or path_gaps(paths, members[net], layout, placed, guards, outside)):
            return failed("network_search_unverified_graph")
    def valid(paths, net, rules, blockers):
        if (not paths or len(paths) > MAX_SEGMENTS or not all(
                w.get("template_segment") and w.get("template_source")
                and w["net"] == net and len(w["points"]) == 2 for w in paths)):
            return False
        key = (net, rules, digest(paths))
        if key not in checks:
            if len(checks) >= MAX_VALIDATIONS:
                rejected.add("network_search_validation_limit")
                return False
            count = len({tuple(p) for w in paths for p in w["points"]})
            trial_edges = [dict(w["template_source"], points=w["points"]) for w in paths]
            checks[key] = (graph_preserved(trial_edges, anchors[net], count, layout["grid"])
                           and not path_gaps(paths, members[net], layout, placed, guards, blockers))
        return checks[key]

    serial = 0
    queue = [(0, serial, original, {n: () for n in original}, {}, False)]
    seen, cache = set(), {}
    alternatives = {n: [(ws, [dict(code="target_network_original_candidate")])]
                    for n, ws in original.items()}
    while queue:
        if expanded >= MAX_NODES:
            return failed("network_search_node_limit")
        _, _, routes, constraints, details, alternate = heappop(queue)
        expanded += 1
        groups = conflict_groups(routes, layout.get("clearance", 0))
        if not groups:
            all_wires = outside + [w for ws in routes.values() for w in ws]
            if any(path_gaps(ws, members[n], layout, placed, guards, all_wires)
                   for n, ws in routes.items()):
                rejected.add("network_search_final_check")
                continue
            changed = sorted(n for n in routes if _signature(routes[n]) != _signature(original[n]))
            diagnostics.append(dict(code="target_network_search_applied", nodes=expanded,
                                    replans=replans, changed_nets=changed,
                                    constraints=sum(map(len, constraints.values())),
                                    repairs=[dict(net=n, **details[n][-1]) for n in changed],
                                    **coverage()))
            return routes
        pair_index = -1
        while groups:
            pair_index += 1
            conflict = select_conflict(groups, visits)
            groups.remove(conflict)
            before = transitions
            left, right = conflict["nets"]
            visits[left, right] = visits.get((left, right), 0) + 1
            if conflict["truncated"]:
                rejected.add("network_search_witness_limit")
            for witness_index, (a, b) in enumerate(conflict["witnesses"]):
                witness_trials += 1
                before_witness = transitions
                last_conflict = dict(nets=[left, right],
                                     segments=[[list(p) for p in ps] for ps in (a, b)])
                for net, other, segment in ((left, right, b), (right, left, a)):
                    constraint = (other, tuple(sorted(tuple(p) for p in segment)))
                    if constraint in constraints[net] or len(constraints[net]) >= MAX_CONSTRAINTS:
                        rejected.add("network_search_constraint_limit")
                        continue
                    rules = tuple(sorted((*constraints[net], constraint)))
                    updated = dict(constraints, **{net: rules})
                    key = tuple((n, updated[n]) for n in sorted(updated))
                    blockers = outside + [dict(net=n, points=[list(p) for p in ps])
                                          for n, ps in rules]
                    if (net, rules) not in cache:
                        # Complete candidates were all built from the same original
                        # graph. Revalidate against EVERY accumulated constraint and
                        # fixed obstacle before spending another local solver call.
                        reusable = next(
                            ((ws, ds) for ws, ds in alternatives[net]
                             if valid(ws, net, rules, blockers)),
                            None)
                        if reusable:
                            reused += 1
                            cache[net, rules] = reusable
                        else:
                            if replans >= MAX_REPLANS:
                                rejected.add("network_search_replan_limit")
                                continue
                            extra = alternate or pair_index > 0 or witness_index > 0
                            if extra and fallback_replans >= MAX_FALLBACK_REPLANS:
                                rejected.add("network_search_fallback_replan_limit")
                                continue
                            fallback_replans += extra
                            replans += 1
                            net_replans[net] = net_replans.get(net, 0) + 1
                            detail = []
                            repaired = repair_paths(edges[net], members[net], layout, placed,
                                                    guards, blockers, detail)
                            cache[net, rules] = repaired, detail
                    options = [cache[net, rules]]
                    options += [(ws, ds) for ws, ds in alternatives[net]
                                if ws is not options[0][0] and valid(ws, net, rules, blockers)]
                    if len(options) > MAX_CHOICES:
                        rejected.add("network_search_choice_limit")
                    options = options[:MAX_CHOICES]
                    for option_index, (repaired, detail) in enumerate(options):
                        if not repaired:
                            rejected.add("network_search_no_local_candidate")
                            local_failures.update(r for d in detail for r in d.get("reasons", []))
                            continue
                        if not valid(repaired, net, rules, blockers):
                            rejected.add("network_search_invalid_local_candidate")
                            continue
                        if not any(_signature(ws) == _signature(repaired)
                                   for ws, _ in alternatives[net]):
                            alternatives[net].append((repaired, detail))
                        trial = dict(routes, **{net: repaired})
                        if sum(map(len, trial.values())) > MAX_TOTAL_SEGMENTS:
                            rejected.add("network_search_work_limit")
                            continue
                        child_alternate = (alternate or pair_index > 0 or witness_index > 0
                                           or option_index > 0)
                        state = (key, child_alternate,
                                 tuple((n, _signature(trial[n])) for n in sorted(trial)))
                        if state in seen:
                            continue
                        seen.add(state)
                        if transitions >= MAX_TRANSITIONS or len(queue) >= MAX_QUEUED:
                            rejected.add("network_search_frontier_limit")
                            continue
                        transitions += 1
                        choice_branches += option_index > 0
                        serial += 1
                        cost = sum(abs(w["points"][1][k] - w["points"][0][k])
                                   for ws in trial.values() for w in ws for k in (0, 1))
                        heappush(queue, (cost, serial, trial, updated,
                                         dict(details, **{net: detail}),
                                         child_alternate))
                if transitions > before_witness:
                    break
            if transitions > before:
                break
            stalled_pairs += 1
    return failed("network_search_replan_limit" if "network_search_replan_limit" in rejected
                  else "network_search_no_legal_combination")
