"""Recover pending graphs via atomic conflict search with verified neighbors."""

from .circuit_geometry_schema import bounds, overlap
from .circuit_port_routing import stub_port
from .template_route_network_search import MAX_NETS, network_paths
from .template_route_paths import path_gaps

MAX_SEARCHES = 2


def recover_networks(planned, report, by_net, port_stubs, pending, layout, placed, pins):
    """Search only after simpler coordination, keeping all other wires fixed."""
    rows = {r["net"]: r for r in report}
    ports = [w for ws in port_stubs.values() for w in ws]
    members = {n: [m for m in ms if not stub_port(m, layout)] for n, ms in by_net.items()}
    attempts = 0
    for net in sorted(pending):
        if rows[net]["status"] in {"reused", "rerouted"}:
            continue
        candidate = pending[net]
        conflicts = {n for n, ws in planned.items() if n != net and any(
            overlap(bounds(w["points"]), bounds([a, b]), layout.get("clearance", 0))
            for w in candidate for v in ws for a, b in zip(v["points"], v["points"][1:]))}
        diagnostics, result = [], None
        if not conflicts or len(conflicts) + 1 > MAX_NETS:
            reason = "network_group_size_limit"
        elif any(rows[n]["status"] not in {"reused", "rerouted"} for n in conflicts):
            reason = "network_group_unverified_neighbor"
        elif attempts >= MAX_SEARCHES:
            reason = "network_group_search_limit"
        else:
            attempts += 1
            original = {n: planned[n] for n in sorted(conflicts)}
            original[net] = candidate
            outside = [w for n, ws in planned.items() if n not in original for w in ws] + ports
            result = network_paths(original, members, layout, placed, pins, outside, diagnostics)
            reason = "network_group_no_legal_candidate"
        if result:
            trial = dict(planned, **result)
            all_wires = [w for ws in trial.values() for w in ws] + ports
            if any(path_gaps(trial[n], members[n], layout, placed, pins, all_wires)
                   for n in trial if n == net or rows[n]["status"] in {"reused", "rerouted"}):
                reason = "network_group_final_check"
            else:
                planned.update(result)
                changed = diagnostics[-1]["changed_nets"]
                for n in sorted(set(changed) | {net}):
                    rows[n].update(status="rerouted", reasons=[], segments=len(result[n]),
                                   method="coordinated_network_search")
                    rows[n].pop("transform", None)
                    rows[n]["diagnostics"] = (rows[n].get("diagnostics", []) + [
                        dict(d, stage="final_network_search", recovered_net=net)
                        for d in diagnostics])[-32:]
                continue
        if not diagnostics or result:
            diagnostics.append(dict(code="target_network_search_exhausted", reasons=[reason]))
        rows[net]["diagnostics"] = (rows[net].get("diagnostics", []) + [
            dict(d, stage="final_network_search") for d in diagnostics])[-32:]
