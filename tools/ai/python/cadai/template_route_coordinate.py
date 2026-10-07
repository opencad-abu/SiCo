"""Atomically recover proved pending graphs by adjusting neighboring branches."""

from .circuit_geometry_schema import bounds, overlap
from .circuit_port_routing import stub_port
from .template_route_chains import chain_paths
from .template_route_networks import recover_networks
from .template_route_paths import graph_preserved, path_gaps

MAX_PENDING = 8
MAX_NEIGHBORS = 2


def coordinate_routes(planned, report, by_net, port_stubs, pending, layout, placed, pins):
    """Only source-proved pending graphs qualify; fallback wire geometry never does.

    Try each pending net once. Candidate plus neighbors commit together only
    after checking every successful net against the latest complete wire set.
    """
    rows = {row["net"]: row for row in report}
    members = {net: [m for m in ms if not stub_port(m, layout)] for net, ms in by_net.items()}
    ports = [w for ws in port_stubs.values() for w in ws]
    for net in sorted(pending)[:MAX_PENDING]:
        if rows[net]["status"] in {"reused", "rerouted"}:
            continue
        candidate = pending[net]
        edges = [dict(w["template_source"], points=w["points"]) for w in candidate]
        anchors = [dict(xy=m["points"][0], endpoint=m["endpoint"]) for m in members[net]]
        count = len({tuple(p) for e in edges for p in e["points"]})
        if (not graph_preserved(edges, anchors, count, layout["grid"])
                or path_gaps(candidate, members[net], layout, placed, pins)):
            continue
        conflicts = set()
        for other, wires in planned.items():
            if other != net and any(overlap(bounds(w["points"]), bounds([a, b]),
                                           layout.get("clearance", 0))
                                    for w in candidate for v in wires
                                    for a, b in zip(v["points"], v["points"][1:])):
                conflicts.add(other)
        diagnostics = []
        reason = "coordinated_no_legal_candidate"
        trial = dict(planned, **{net: candidate})
        changed = {}
        if len(conflicts) > MAX_NEIGHBORS:
            reason = "coordinated_neighbor_limit"
        elif any(rows[n]["status"] not in {"reused", "rerouted"} for n in conflicts):
            reason = "coordinated_unverified_neighbor"
        else:
            for other in sorted(conflicts):
                if not all(w.get("template_segment") for w in trial[other]):
                    break
                edges = [dict(w["template_source"], points=w["points"]) for w in trial[other]]
                detail = []
                repaired = chain_paths(edges, members[other], layout, placed, pins,
                                       [w for ws in trial.values() for w in ws] + ports, detail)
                diagnostics.extend(dict(d, neighbor=other) for d in detail)
                if not repaired:
                    break
                trial[other] = repaired
                changed[other] = detail
            else:
                all_wires = [w for ws in trial.values() for w in ws] + ports
                successful = [n for n in trial if n == net or
                              rows[n]["status"] in {"reused", "rerouted"}]
                if not any(path_gaps(trial[n], members[n], layout, placed, pins, all_wires)
                           for n in successful):
                    planned.update(trial)
                    for n in [net, *sorted(changed)]:
                        row = rows[n]
                        row.update(status="rerouted", reasons=[], segments=len(trial[n]),
                                   method="coordinated_deformation" if n == net else
                                   "coordinated_branch_chain")
                        row.pop("transform", None)
                        detail = diagnostics if n == net else changed[n]
                        row["diagnostics"] = (row.get("diagnostics", []) + detail + [dict(
                            code="target_coordinated_repair_applied", stage="final_coordination",
                            recovered_net=net, neighbors=sorted(conflicts))])[:32]
                    continue
        rows[net]["diagnostics"] = (rows[net].get("diagnostics", []) + diagnostics + [dict(
            code="target_coordinated_repair_exhausted", stage="final_coordination",
            reasons=[reason])])[:32]
    recover_networks(planned, report, by_net, port_stubs,
                     {n: pending[n] for n in sorted(pending)[:MAX_PENDING]}, layout, placed, pins)
