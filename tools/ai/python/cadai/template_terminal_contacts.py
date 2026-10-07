"""Preserve distinct terminal identities that share one physical device contact."""

from collections import defaultdict

from .circuit_spec_schema import canonical


def target_endpoint(endpoint, use):
    if "port" in endpoint:
        return {"port": use["port_map"][endpoint["port"]]}
    row = next(d for d in use["devices"] if d["source_device"] == endpoint["device"])
    return {"instance": row["instance"], "terminal": row["terminal_map"][endpoint["terminal"]]}


def shared_contacts(anchors):
    """Return proven same-device groups, or None for ambiguous coincidences.

    Input belongs to one verified net. Port contacts and contacts between
    different devices remain conservative; electrical identities are never
    deduplicated just because their coordinates match.
    """
    by_point, seen = defaultdict(list), set()
    for anchor in anchors:
        endpoint = anchor.get("endpoint")
        if endpoint is not None:
            key = canonical(endpoint)
            if key in seen:
                return None
            seen.add(key)
        by_point[tuple(anchor["xy"])].append(endpoint)
    groups = []
    for point, endpoints in sorted(by_point.items()):
        if len(endpoints) < 2:
            continue
        owners, terminals = set(), set()
        for ep in endpoints:
            if not isinstance(ep, dict) or set(ep) not in (
                {"device", "terminal"}, {"instance", "terminal"}
            ):
                return None
            field = "device" if "device" in ep else "instance"
            if not all(isinstance(ep[k], str) and ep[k] for k in (field, "terminal")):
                return None
            owners.add((field, ep[field]))
            terminals.add(ep["terminal"])
        if len(owners) != 1 or len(terminals) != len(endpoints):
            return None
        groups.append({"xy": list(point), "endpoints": sorted(endpoints, key=canonical)})
    return groups


def mapping_gaps(anchors, use, members):
    """The mapped source and target contact partitions must be identical."""
    if shared_contacts(anchors) is None or shared_contacts([
        {"xy": m["points"][0], "endpoint": m["endpoint"]} for m in members
    ]) is None:
        return ["ambiguous_coincident_source_terminals"]
    old_to_new, new_to_old, seen, targets = defaultdict(set), defaultdict(set), set(), {}
    for member in members:
        key = canonical(member["endpoint"])
        if key in targets:
            return ["source_target_endpoint_coverage_differs"]
        targets[key] = tuple(member["points"][0])
    for anchor in anchors:
        ep = anchor["endpoint"]
        try:
            key = canonical(target_endpoint(ep, use))
            point = targets[key]
        except (KeyError, StopIteration):
            return ["source_target_endpoint_coverage_differs"]
        if key in seen:
            return ["source_target_endpoint_coverage_differs"]
        seen.add(key)
        old = tuple(anchor["xy"])
        old_to_new[old].add(point)
        new_to_old[point].add(old)
    if seen != set(targets):
        return ["source_target_endpoint_coverage_differs"]
    if any(len(values) != 1 for values in old_to_new.values()):
        return ["source_shared_terminal_contact_separated"]
    if any(len(values) != 1 for values in new_to_old.values()):
        return ["target_terminal_contacts_collapsed"]
    return []
