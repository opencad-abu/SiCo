"""Summarize segment collisions and fairly select a network pair to expand."""

from .circuit_geometry_schema import bounds, overlap

MAX_WITNESSES = 4


def conflict_groups(routes, clearance):
    """Keep bounded stable segment witnesses and a hit count per network pair.

    Inputs have passed the network search's graph/size guards. Precomputed
    segment boxes avoid repeatedly sorting paths or bounding whole polylines.
    At most six summaries exist for the search's four participating networks.
    """
    segments = {n: sorted((tuple(sorted(tuple(p) for p in w["points"])),
                           bounds(w["points"])) for w in ws) for n, ws in routes.items()}
    nets, result = sorted(routes), []
    for i, left in enumerate(nets):
        for right in nets[i+1:]:
            count, witnesses = 0, []
            for a, abox in segments[left]:
                for b, bbox in segments[right]:
                    if overlap(abox, bbox, clearance):
                        count += 1
                        if len(witnesses) < max(1, MAX_WITNESSES):
                            witnesses.append((a, b))
            if count:
                result.append(dict(nets=(left, right), count=count, segments=witnesses[0],
                                   witnesses=witnesses, truncated=count > len(witnesses)))
    return result


def select_conflict(groups, visits):
    """Give unvisited pairs a turn before repeating an already expanded pair.

    Prefer fewer segment contacts at equal visit count, then geometry and names
    for deterministic ties. History only changes search priority, never guards.
    """
    return min(groups, key=lambda r: (visits.get(r["nets"], 0), r["count"],
                                      r["segments"], r["nets"]))
