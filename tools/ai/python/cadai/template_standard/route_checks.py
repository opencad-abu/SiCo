"""Recheck flat paths against the sole electrical graph and captured contact semantics."""

from collections import defaultdict
from itertools import combinations

from ..template_schema import TemplateError
from ..template_wire_contacts import source_contacts
from ..template_wire_graph import connect_net, crossing, on_segment
from .codec import fail


def endpoint(row):
    return (row["device"], row["terminal"]) if "device" in row else ("port", row["port"])


def positions(route, place):
    return {
        key: (row["xy"] if "xy" in row else place["anchors"][row["anchors"][0]]["xy"])
        for key, row in route["nodes"].items()
    }


def touches(a, b, c, d):
    return (
        crossing(a, b, c, d) is not None
        or any(on_segment(p, a, b) for p in (c, d))
        or any(on_segment(p, c, d) for p in (a, b))
    )


def crosses_body(a, b, box):
    # Integer source DBU; boundary tangents are allowed, positive interior length is not.
    axis = 0 if a[1] == b[1] else 1
    fixed = 1 - axis
    return box[0][fixed] < a[fixed] < box[1][fixed] and max(
        min(a[axis], b[axis]), box[0][axis]
    ) < min(max(a[axis], b[axis]), box[1][axis])


def crossing_rows(route, place, isolated=()):
    """Enumerate every contact; only pre-attested interior X crossings gain isolation."""
    xy = positions(route, place)
    rows = {}
    for (left, a), (right, b) in combinations(route["segments"].items(), 2):
        p, q, r, s = (xy[a["from"]], xy[a["to"]], xy[b["from"]], xy[b["to"]])
        if not touches(p, q, r, s):
            continue
        shared = {a["from"], a["to"]} & {b["from"], b["to"]}
        row = dict(segments=[left, right], relation="unknown")
        # Split edges may share a single endpoint; duplicates/overlaps remain unknown.
        if (
            len(shared) == 1
            and a["net"] == b["net"]
            and not any(on_segment(v, p, q) and v not in (p, q) for v in (r, s))
            and not any(on_segment(v, r, s) and v not in (r, s) for v in (p, q))
        ):
            row.update(relation="joined", node=next(iter(shared)))
        elif frozenset((left, right)) in isolated:
            row["relation"] = "isolated"
        rows["x" + str(len(rows))] = row
    return rows


def issues(docs):
    route, place, top = docs.get("routing"), docs.get("placement"), docs.get("topology")
    if not route or not place or not top:
        return ["routing_facts_missing"]
    if len(route["segments"]) > 512 or len(place["anchors"]) > 4096:
        return ["routing_work_budget"]
    reasons = set()
    observed = (
        docs["collection"]["assets"]
        .get("schematic", {})
        .get("observations", {})
        .get("wire_ownership")
        == "present"
    )
    if not observed:
        reasons.add("wire_ownership_unverified")
    xy, lines, anchors = positions(route, place), defaultdict(list), defaultdict(list)
    nets = {endpoint(row): row["net"] for row in top["connections"].values()}
    used, anchor_counts = set(), defaultdict(int)
    for key, row in place["anchors"].items():
        net = nets[endpoint(row)]
        anchor_counts[endpoint(row)] += 1
        if isinstance(net, str):
            ep = {k: row[k] for k in ("device", "terminal", "port") if k in row}
            anchors[net].append(dict(xy=row["xy"], endpoint=ep))
    if set(anchor_counts) != set(nets) or any(n != 1 for n in anchor_counts.values()):
        reasons.add("missing_or_multiple_endpoint_anchors")
    for i, (key, row) in enumerate(route["segments"].items()):
        a, b = xy[row["from"]], xy[row["to"]]
        lines[row["net"]].append(dict(points=[a, b], shape=i, segment=0))
        for node in (row["from"], row["to"]):
            for anchor in route["nodes"][node].get("anchors", []):
                if nets[endpoint(place["anchors"][anchor])] != row["net"]:
                    reasons.add("anchor_net_mismatch")
                used.add(anchor)
        if any(crosses_body(a, b, r["bbox"]) for r in place["instances"].values()):
            reasons.add("source_wire_crosses_device_body")
    if used != set(place["anchors"]):
        reasons.add("endpoint_not_attached_to_node")
    isolated = set()
    try:
        contact_gaps, proof = source_contacts(lines, anchors, observed=observed)
        ids = list(route["segments"])
        for rows in proof.values():
            for row in rows:
                isolated.add(frozenset((ids[row["segment"][0]], ids[row["other_segment"][0]])))
        for net in top["nets"]:
            reasons.update(contact_gaps[net])
            reasons.update(connect_net(lines[net], anchors[net], shared_terminals=observed)["gaps"])
    except TemplateError as exc:
        reasons.add(str(exc))
    expected = crossing_rows(route, place, isolated)

    def keyed(rows):
        return {
            tuple(sorted(r["segments"])): {k: v for k, v in r.items() if k != "segments"}
            for r in rows.values()
        }

    if keyed(expected) != keyed(route["crossings"]) or len(keyed(route["crossings"])) != len(
        route["crossings"]
    ):
        reasons.add("crossing_evidence_incomplete_or_inconsistent")
    if any(r["relation"] == "unknown" for r in route["crossings"].values()):
        reasons.add("unknown_source_crossing")
    return sorted(reasons)


def validate_receipt(docs):
    checks = docs["qualification"]["checks"]
    if checks["routing-reference-v1"]["result"] != "passed":
        return
    if checks["placement-reference-v1"]["result"] != "passed":
        fail("routing requires qualified placement")
    gaps = [g for g in docs["collection"]["gaps"].values() if g["part"] == "routing.json"]
    if issues(docs) or gaps:
        fail("passed routing has incomplete or conflicting path evidence")
