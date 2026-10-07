"""Bounded source geometry relations; no inferred electrical matching or functional groups."""

import math
from itertools import combinations

from .circuit_geometry_schema import MATRICES
from .template_coords import instance_position, is_integer_space, is_point, valid_box
from .template_device_axes import device_axes
from .template_port_relations import port_relations
from .template_relation_geometry import MIRROR_VERTICAL as MIRROR_VERTICAL
from .template_relation_geometry import _grouped, _net_names, _spacing
from .template_routing_style import routing_style as routing_style
from .template_schema import TemplateUnavailable

RELATIONS_SCHEMA = "cad.template.relations.v2"
MAX_INSTANCES = 256
MAX_PAIRS = 64
MAX_GROUPS = 32
EDGE_TOLERANCE = 1


class RelationEvidenceUnavailable(TemplateUnavailable):
    """Stored relation evidence cannot be derived within its supported bounds."""

    def __init__(self, code, detail):
        self.code = code
        super().__init__(detail)


def _bounded_inputs(record, asset):
    """Bound work before any quadratic derivation, without taking a partial inventory."""
    topology = record.get("topology") or {}
    reference = asset.get("wire_reference") or {}
    for name, rows, limit in (
        ("instances", asset.get("instances", []), MAX_INSTANCES),
        ("devices", topology.get("devices", []), MAX_INSTANCES),
        ("nets", topology.get("nets", []), 256),
        ("ports", topology.get("ports", []), 128),
        ("pins", asset.get("pins", []), 1024),
        ("geometry", reference.get("geometry", []), MAX_INSTANCES),
        ("port anchors", reference.get("ports", []), 1024),
    ):
        if not isinstance(rows, list) or len(rows) > limit:
            raise RelationEvidenceUnavailable("relations_work_budget",
                                              "relations work budget exceeded: " + name)
    for device in topology.get("devices", []):
        if len(device.get("pins", [])) > 64:
            raise RelationEvidenceUnavailable("relations_work_budget",
                                              "relations work budget exceeded: device pins")
    for row in reference.get("geometry", []):
        terms = row.get("terminals", [])
        if len(terms) > 64 or any(len(t.get("anchors", [])) > 8 for t in terms):
            raise RelationEvidenceUnavailable("relations_work_budget",
                                              "relations work budget exceeded: terminal anchors")
        if len({t["name"] for t in terms}) != len(terms):
            raise RelationEvidenceUnavailable("relation_identity_ambiguous",
                                              "duplicate source geometry terminal")
    for rows, field in ((topology.get("devices", []), "id"),
                        (reference.get("geometry", []), "device")):
        keys = [r[field] for r in rows]
        if len(set(keys)) != len(keys):
            raise RelationEvidenceUnavailable("relation_identity_ambiguous",
                                                  "duplicate relations " + field)


def _instances(record, asset, gaps):
    kinds = {d["id"]: d.get("kind", "unknown")
             for d in record.get("topology", {}).get("devices", [])}
    instances, names, observed = {}, set(), set()
    origin = asset.get("origin")
    for row in asset.get("instances", []):
        device = row.get("device") or row.get("source_name")
        name = row.get("source_name") or device
        if device in observed or name in names:
            raise RelationEvidenceUnavailable("relation_identity_ambiguous",
                                              "duplicate placement device or source name")
        observed.add(device)
        names.add(name)
        xy = instance_position(row)
        if xy is None or not device:
            gaps.append(dict(code="placement_incomplete", device=device))
            continue
        box = row.get("bbox") if valid_box(row.get("bbox")) else None
        if box is None:
            gaps.append(dict(code="bounding_box_missing", device=device))
        if row.get("orient") not in MATRICES:
            gaps.append(dict(code="source_orientation_unavailable", device=device))
        # Legacy source boxes and absolute xy use the same frame. Integer assets
        # already normalize both against their captured anchor.
        if not is_integer_space(asset) and is_point(origin):
            if row.get("relative_xy") != xy:
                xy = [xy[k] - origin[k] for k in (0, 1)]
            if box is not None:
                box = [[p[k] - origin[k] for k in (0, 1)] for p in box]
        instances[device] = dict(device=device, name=name, kind=kinds.get(device, "unknown"),
                                 xy=xy, orient=row.get("orient"), bbox=box)
    gaps.extend(dict(code="placement_incomplete", device=d)
                for d in sorted(kinds.keys() - observed))
    return {key: instances[key] for key in sorted(instances)}


def _pairs(record, instances, rows):
    names = _net_names(record)
    nets = {d["id"]: {names.get(p["net"], p["net"]) for p in d.get("pins", []) if p.get("net")}
            for d in record.get("topology", {}).get("devices", [])}
    pairs = []
    for row in rows:
        members = sorted(row["members"], key=lambda n: (instances[n]["xy"][0], n))
        for left, right in zip(members, members[1:]):
            a, b = instances[left], instances[right]
            if MIRROR_VERTICAL.get(a["orient"]) != b["orient"] or a["orient"] not in MATRICES:
                continue
            gap = abs(a["xy"][0] - b["xy"][0])
            if not gap:
                continue
            # Keep stable source-name ordering, independent of asset order.
            left, right = sorted((left, right))
            pairs.append(dict(a=left, b=right, axis="vertical", gap=gap,
                              same_kind=a["kind"] not in (None, "unknown")
                              and a["kind"] == b["kind"],
                              shared_nets=sorted(nets.get(a["device"], set())
                                                 & nets.get(b["device"], set()))))
    return sorted(pairs, key=lambda p: (p["gap"], p["a"], p["b"]))


def _alignment(instances):
    result = []
    for left, right in combinations(sorted(instances), 2):
        a, b = instances[left], instances[right]
        if a["bbox"] is None or b["bbox"] is None:
            continue
        for edge, index in (("left", 0), ("right", 1)):
            delta = a["bbox"][index][0] - b["bbox"][index][0]
            if abs(delta) <= EDGE_TOLERANCE:
                result.append(dict(a=left, b=right, edge=edge, delta=delta))
                break
    return result


def relations(record):
    """Derive a versioned view without changing the stored template or its reference."""
    asset = record.get("assets", {}).get("schematic")
    if not asset:
        raise RelationEvidenceUnavailable("schematic_relations_unavailable",
                                          "this derived view requires a stored schematic asset")
    _bounded_inputs(record, asset)
    gaps = []
    instances = _instances(record, asset, gaps)
    usable = {r["name"]: r for r in instances.values()}
    rows, columns = _grouped(usable, 1), _grouped(usable, 0)
    pairs = _pairs(record, usable, rows)
    groups = [dict(kind="mirrored_pair", members=[p["a"], p["b"]],
                   evidence="source_geometry", strength="preference") for p in pairs]
    groups.extend(dict(kind="row", members=r["members"], evidence="source_geometry",
                       strength="preference") for r in rows if len(r["members"]) >= 3)
    coverage = {}

    def bounded(name, values, limit):
        total, returned = len(values), min(len(values), limit)
        coverage[name] = dict(total=total, returned=returned, omitted=total - returned)
        if total > returned:
            gaps.append(dict(code="relation_list_truncated", relation=name,
                             total=total, returned=returned, omitted=total - returned))
        return values[:limit]

    pairs = bounded("mirrored_pairs", pairs, MAX_PAIRS)
    groups = bounded("groups", groups, MAX_GROUPS)
    alignment = bounded("alignment", _alignment(usable), MAX_PAIRS)
    if is_integer_space(asset) and not (type(asset.get("dbu_per_uu")) in (int, float)
                                       and math.isfinite(asset["dbu_per_uu"])
                                       and asset["dbu_per_uu"] > 0):
        gaps.append(dict(code="coordinate_scale_missing"))
    axes = device_axes(asset, gaps=gaps)
    return dict(
        schema=RELATIONS_SCHEMA, template_ref=record.get("template_ref"),
        unit="source_dbu" if is_integer_space(asset) else "source_user_units",
        dbu_per_uu=asset.get("dbu_per_uu") if is_integer_space(asset) else None,
        instances=list(instances.values()), ports=port_relations(record),
        device_axes=axes, rows=rows, columns=columns,
        mirrored_pairs=pairs, alignment=alignment, groups=groups,
        spacing=_spacing(usable) if usable else {"nearest": {}, "classes": None},
        coverage=coverage, gaps=sorted(gaps, key=lambda g: (g["code"], str(g.get("device", "")),
                                                         g.get("relation", ""),
                                                         g.get("terminal", ""), g.get("axis", ""))),
        use="relative_relations_only; recompute_with_target_master_geometry; "
            "geometric_groups_not_electrical_matching_or_functional_groups",
    )
