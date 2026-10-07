"""Reconcile verified template routes against the complete final wire set."""

from .circuit_port_routing import stub_port
from .template_route_paths import path_gaps
from .template_route_repair import repair_paths

MAX_NET_REPAIRS = 16


def finalize_routes(planned, report, by_net, port_stubs, layout, placed, pins):
    """Try each successful net once before monotonic fallback, never repair stubs.

    Repairs check the latest complete result, including independent port stubs.
    Later fallbacks may introduce new obstacles; the next pass rechecks successes.
    A fallback cannot become successful here, so at most N + 1 passes suffice.
    """
    attempted = set()
    ports = [w for rows in port_stubs.values() for w in rows]
    for _ in range(len(report) + 1):
        changed = False
        for row in report:
            if row["status"] not in {"reused", "rerouted"}:
                continue
            net = row["net"]
            members = [w for w in by_net[net] if not stub_port(w, layout)]
            all_wires = [w for rows in planned.values() for w in rows] + ports
            failures = path_gaps(planned[net], members, layout, placed, pins, all_wires)
            if not failures:
                continue
            if failures == ["template_paths_different_nets_touch"] and net not in attempted:
                diagnostics = []
                repaired = None
                if len(attempted) >= MAX_NET_REPAIRS:
                    diagnostics.append(dict(code="final_net_repair_budget_exhausted"))
                elif all(w.get("template_segment") for w in planned[net]):
                    attempted.add(net)
                    edges = [dict(w["template_source"], points=w["points"]) for w in planned[net]]
                    repaired = repair_paths(edges, members, layout, placed, pins, all_wires,
                                            diagnostics)
                row["diagnostics"] = (row.get("diagnostics", []) + [
                    dict(d, stage="final_net_check") for d in diagnostics])[:32]
                if repaired:
                    planned[net] = repaired
                    method = ("final_conflict_component" if any(
                        d["code"] == "target_conflict_component_applied" for d in diagnostics
                    ) else "final_branch_chain" if any(
                        d["code"] == "target_branch_chain_applied" for d in diagnostics
                    ) else "final_free_track" if any(
                        d["code"] == "target_free_track_applied" for d in diagnostics
                    ) else "final_graph_detour")
                    row.update(status="rerouted", method=method, reasons=[],
                               segments=len(repaired))
                    row.pop("transform", None)
                    continue
            row.update(status="stub" if layout["routing"].get("fallback", "reject") == "stub"
                       else "rejected", reasons=failures)
            for field in ("method", "transform", "segments"):
                row.pop(field, None)
            planned[net] = members
            changed = True
        if not changed:
            break
