"""Remove proven source port-only branches before internal template path reuse."""

from .template_wire_graph import connect_net


def device_reference(src):
    """Prune only proven port-only leaf branches from a complete source graph.

    Invalid source evidence is retained unchanged and will fail normal routing
    validation. Removing a port never removes a device terminal or its branch.
    """
    if src["gaps"]:
        return src
    anchors = [a for a in src["anchors"] if "port" not in a["endpoint"]]
    protected = {tuple(a["xy"]) for a in anchors}
    edges = list(src["edges"])
    while edges:
        degree = {}
        for edge in edges:
            for point in edge["points"]:
                key = tuple(point)
                degree[key] = degree.get(key, 0) + 1
        leaves = {p for p, count in degree.items() if count == 1 and p not in protected}
        if not leaves:
            break
        edges = [e for e in edges if not any(tuple(p) in leaves for p in e["points"])]
    checked = connect_net(edges, anchors, shared_terminals="terminal_contacts" in src)
    return {**src, **checked, "anchors": anchors, "eligible": not checked["gaps"]}
