"""Deform proven orthogonal source graphs along target terminal coordinate tracks."""

from .circuit_geometry_schema import transform
from .circuit_spec_schema import canonical
from .template_route_axis import axis_map as _axis_map
from .template_route_axis import local_order_maps
from .template_route_paths import graph_preserved as _graph_preserved
from .template_route_paths import instance_frame, path_gaps, same, target_endpoint, wire_paths
from .template_route_repair import repair_paths
from .template_terminal_contacts import mapping_gaps


def _orientation(src, source, use, layout):
    devices = {a["endpoint"]["device"] for a in src["anchors"] if "device" in a["endpoint"]}
    frames = [instance_frame(source, row, layout) for row in use["devices"]
              if row["source_device"] in devices]
    orientations = {f[0] for f in frames if f is not None}
    return next(iter(orientations)) if frames and all(frames) and len(orientations) == 1 else None


def _target_anchor(member, geometry, layout):
    """Deformation needs actual target geometry, never an inferred terminal position."""
    endpoint = member["endpoint"]
    if "port" in endpoint:
        return same(layout.get("ports", {}).get(endpoint["port"], {}).get("xy", []),
                    member["points"][0])
    key = endpoint["instance"]
    terms = geometry.get(key, {}).get("terminals", [])
    anchors = next((t["anchors"] for t in terms if t["name"] == endpoint["terminal"]), [])
    chosen = layout.get("anchor_choices", {}).get(key, {}).get(endpoint["terminal"])
    if chosen is not None:
        anchors = [a for a in anchors if a["id"] == chosen]
    if len(anchors) != 1:
        return False
    place = layout["instances"][key]
    return same(transform(anchors[0]["xy"], place["orientation"], place["xy"]),
                member["points"][0])


def deform_paths(src, source, use, members, geometry, layout, placed, pins, reserved,
                 diagnostics=None, *, pending=None):
    """Map a complete source graph without changing adjacency or endpoint identity.

    Prefer global monotone maps; unrelated track reversals may use one candidate
    preserving every actual edge's direction. Both require full graph and target
    checks. Bounded edge, track and branch repairs may resolve foreign-wire
    collisions; terminals stay fixed.
    """
    diagnostics = diagnostics if diagnostics is not None else []
    if src["gaps"]:
        diagnostics.append({"code": "source_attachment_gaps", "reasons": list(src["gaps"])})
        return None
    if not src["edges"]:
        diagnostics.append({"code": "source_edges_empty"})
        return None
    contact_gaps = mapping_gaps(src["anchors"], use, members)
    if contact_gaps:
        diagnostics.append({"code": "terminal_contact_mapping_invalid", "reasons": contact_gaps})
        return None
    orientation = _orientation(src, source, use, layout)
    if orientation is None:
        diagnostics.append({"code": "common_orientation_unavailable"})
        return None
    invalid = [m["endpoint"] for m in members if not _target_anchor(m, geometry, layout)]
    if invalid:
        diagnostics.append({"code": "target_anchor_unavailable", "endpoints": invalid[:32]})
        return None
    targets = {canonical(m["endpoint"]): m["points"][0] for m in members}
    if {canonical(target_endpoint(a["endpoint"], use)) for a in src["anchors"]} != set(targets):
        diagnostics.append({"code": "source_target_endpoint_coverage_differs"})
        return None
    edges = [{**e, "points": [transform(p, orientation) for p in e["points"]]}
             for e in src["edges"]]
    anchors = [(transform(a["xy"], orientation),
                targets[canonical(target_endpoint(a["endpoint"], use))])
               for a in src["anchors"]]
    axis_diagnostics = []
    maps = [_axis_map({p[k] for e in edges for p in e["points"]},
                      [(old[k], new[k]) for old, new in anchors], layout["grid"],
                      source["dbu_per_uu"], axis_diagnostics, axis="xy"[k]) for k in (0, 1)]
    diagnostics.extend(axis_diagnostics)
    local_order = any(m is None for m in maps)
    if local_order:
        if not axis_diagnostics or any(d["code"] != "axis_track_order_reversed"
                                       for d in axis_diagnostics):
            return None
        maps = local_order_maps(edges, anchors, layout["grid"], source["dbu_per_uu"],
                                diagnostics)
        if maps is None:
            return None
    moved = [{**e, "points": [[maps[k][p[k]] for k in (0, 1)] for p in e["points"]]}
             for e in edges]
    count = len({tuple(p) for e in edges for p in e["points"]})
    target_anchors = [{"xy": m["points"][0], "endpoint": m["endpoint"]} for m in members]
    if not _graph_preserved(moved, target_anchors, count, layout["grid"]):
        diagnostics.append({"code": "target_graph_not_preserved"})
        return None
    paths = wire_paths(moved, members, members[0]["net"])
    gaps = path_gaps(paths, members, layout, placed, pins, reserved)
    if local_order and set(gaps) <= {"template_paths_different_nets_touch"}:
        diagnostics.append(dict(code="target_local_order_graph_verified",
                                edges=len(moved), vertices=count))
    if gaps:
        if set(gaps) <= {"template_paths_different_nets_touch"}:
            if pending is not None:
                # A graph proved against terminals and bodies, retained only
                # for atomic final coordination. Never derived from a stub.
                pending.append(paths)
            repaired = repair_paths(moved, members, layout, placed, pins, reserved, diagnostics)
            if repaired:
                return repaired
        diagnostics.append({"code": "target_path_constraints", "reasons": gaps})
        return None
    return paths
