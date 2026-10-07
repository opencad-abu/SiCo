"""Classify source wire contacts using guarded OA shape/net observations."""

import re
from collections import defaultdict

from .template_schema import TemplateError, digest
from .template_wire_graph import crossing, on_segment

LEGACY_VERSION = "cad.template.wire-reference.v1"
VERSION = "cad.template.wire-reference.v2"
NET_SEMANTICS = "oa_shape_net_v1"


def capture_contact_evidence(header, shapes):
    """Only newly attested captures gain the OA interior-crossing semantics."""
    semantics = header.get("wire_net_semantics")
    if semantics is None:
        return {}
    if semantics != NET_SEMANTICS:
        raise TemplateError("unsupported source wire net semantics")
    return {"wire_net_semantics": semantics, "wire_shapes_digest": digest(shapes)}


def has_contact_evidence(reference, shapes):
    if reference["schema"] == LEGACY_VERSION:
        return False
    if (reference.get("wire_net_semantics") != NET_SEMANTICS
            or reference.get("wire_shapes_digest") != digest(shapes)
            or not isinstance(reference.get("dependency_digest"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", reference["dependency_digest"])):
        raise TemplateError("routing_reference_malformed: invalid source wire contact evidence")
    return True


def source_contacts(lines, anchors, *, observed=False, markers=()):
    """Keep isolated interior X crossings separate; endpoints and T contacts fail.

    OA net names alone are insufficient: even a conflicting T may retain two
    names after schCheck. No terminal or third segment endpoint may occupy the
    crossing. Same-net connectivity remains the responsibility of connect_net.
    """
    all_lines = [(net, s) for net, rows in lines.items() for s in rows]
    if len(all_lines) > 2048:
        raise TemplateError("source exceeds 2048 wire segments")
    if observed and (len(markers) > 2048 or sum(map(len, anchors.values())) > 4096):
        raise TemplateError("source contact evidence exceeds marker/terminal budget")
    vertices = {tuple(p) for _, s in all_lines for p in s["points"]}
    vertices.update(tuple(a["xy"]) for rows in anchors.values() for a in rows)
    gaps, isolated = defaultdict(set), defaultdict(list)
    crossing_count = 0
    for index, (net, line) in enumerate(all_lines):
        a, b = line["points"]
        for other, second in all_lines[index + 1:]:
            if net == other:
                continue
            c, d = second["points"]
            point = crossing(a, b, c, d)
            if observed and point is not None:
                crossing_count += 1
                if crossing_count > 4096:
                    raise TemplateError("source exceeds 4096 wire crossings")
            marked = observed and point is not None and any(
                all(box[0][k] <= point[k] <= box[1][k] for k in (0, 1)) for box in markers)
            if observed and point is not None and point not in vertices and not marked:
                for own, peer, segment, other_segment in (
                    (net, other, line, second), (other, net, second, line)
                ):
                    isolated[own].append({
                        "point": list(point), "other_net": peer,
                        "segment": [segment["shape"], segment["segment"]],
                        "other_segment": [other_segment["shape"], other_segment["segment"]],
                    })
            elif (point is not None or any(on_segment(p, a, b) for p in (c, d))
                  or any(on_segment(p, c, d) for p in (a, b))):
                gaps[net].add("different_source_nets_touch")
                gaps[other].add("different_source_nets_touch")
        # A terminal on another net's wire is a conflict even without a second wire.
        for other, rows in (anchors.items() if observed else ()):
            if other != net and any(on_segment(r["xy"], a, b) for r in rows):
                gaps[net].add("different_source_nets_touch")
                gaps[other].add("different_source_nets_touch")
    return gaps, isolated
