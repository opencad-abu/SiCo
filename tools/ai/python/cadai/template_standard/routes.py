"""Project verified source wire attachment edges, preserving endpoint and crossing identity."""

from ..template_schema import TemplateError
from ..template_wire_attachment import compile_reference
from .geometry import routing as legacy_routing
from .route_checks import crossing_rows, endpoint, issues, positions


def project(record, docs, env, gap):
    place, top = docs["placement"], docs["topology"]
    try:
        attached = compile_reference(record)
    except TemplateError as exc:
        gap("routing", "source", str(exc), "unsupported_feature", "collect_source")
        return unqualified(record, docs, env, gap)
    total = sum(len(r["edges"]) for r in attached["nets"].values())
    if total > 512:
        gap(
            "routing",
            "segments",
            "Source path qualification exceeds 512 edges",
            "unsupported_feature",
            "upgrade_reader",
        )
        return unqualified(record, docs, env, gap)
    out = dict(**env, placement_ref="placement.json", nodes={}, segments={}, crossings={})
    nets = {endpoint(r): r["net"] for r in top["connections"].values()}
    by_net_xy = {}
    for key, row in place["anchors"].items():
        net = nets[endpoint(row)]
        if isinstance(net, str):
            by_net_xy.setdefault((net, tuple(row["xy"])), []).append(key)
    nodes, origins = {}, {}

    def node(point, net):
        key = (net, tuple(point))
        if key not in nodes:
            ident = "v" + str(len(nodes))
            nodes[key] = ident
            anchors = sorted(by_net_xy.get(key, []))
            out["nodes"][ident] = dict(anchors=anchors) if anchors else dict(xy=point)
        return nodes[key]

    for net, row in sorted(attached["nets"].items()):
        for reason in row["gaps"]:
            gap("routing", net, reason, "missing_required", "collect_source")
        for edge in row["edges"]:
            key = "s" + str(len(out["segments"]))
            a, b = edge["points"]
            out["segments"][key] = dict(net=net, **{"from": node(a, net), "to": node(b, net)})
            origins[key] = (edge["shape"], edge["segment"])
    isolated = set()
    for net, row in attached["nets"].items():
        for proof in row.get("isolated_crossings", []):
            own = [k for k, origin in origins.items() if origin == tuple(proof["segment"])]
            other = [k for k, origin in origins.items() if origin == tuple(proof["other_segment"])]
            isolated.update(frozenset((a, b)) for a in own for b in other)
    out["crossings"] = crossing_rows(out, place, isolated)
    for reason in issues(docs | dict(routing=out)):
        gap("routing", "paths", reason, "missing_required", "collect_source")
    return out


def unqualified(record, docs, env, gap):
    """Keep source geometry after incomplete/over-budget compilation, with no contact claim."""
    place = docs["placement"]

    def unresolved(part, obj, reason, *args):
        if reason == "Full source path/contact qualification is not implemented":
            reason = "This source cannot provide fully qualified paths/contact evidence"
        gap(part, obj, reason, *args)

    route = legacy_routing(record, place, env, unresolved)
    xy = positions(route, place)
    nets = {endpoint(r): r["net"] for r in docs["topology"]["connections"].values()}
    for segment in route["segments"].values():
        for key in (segment["from"], segment["to"]):
            row = route["nodes"][key]
            if "anchors" in row:
                anchors = [
                    a
                    for a in row["anchors"]
                    if nets[endpoint(place["anchors"][a])] == segment["net"]
                ]
                route["nodes"][key] = dict(anchors=anchors) if anchors else dict(xy=xy[key])
    return route
