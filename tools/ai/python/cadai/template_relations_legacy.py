"""Frozen relations v1 for deterministic historical projection reconstruction.

Compat only: remove when old L1/L2/L3 projection references no longer need rebuilding.
Current readers and placement must use template_relations.relations. Historical
matched_pair labels and bounded lists are preserved only for immutable projection content.
"""

from __future__ import annotations

import math

from .template_coords import instance_position, is_integer_space
from .template_device_axes import device_axes
from .template_port_relations import port_relations
from .template_relation_geometry import MIRROR_VERTICAL, _grouped, _net_names, _spacing
from .template_schema import TemplateUnavailable

RELATIONS_SCHEMA = "cad.template.relations.v1"
MAX_PAIRS = 64
MAX_GROUPS = 32
EDGE_TOLERANCE = 1  # source units


def _point(value):
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(type(n) in (int, float) and math.isfinite(n) for n in value)
    )


def _box(value):
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(_point(p) for p in value)
        and all(value[0][i] <= value[1][i] for i in (0, 1))
    )


def _schematic(record):
    asset = record.get("assets", {}).get("schematic")
    if not asset:
        raise TemplateUnavailable("this derived view requires a stored schematic asset")
    return asset


def _relative(asset, instance):
    """Placement relative to the asset anchor, or None when unavailable."""
    xy = instance_position(instance)
    if not _point(xy):
        return None
    if is_integer_space(asset) or instance.get("relative_xy") == xy:
        return list(xy)
    origin = asset.get("origin")
    if _point(origin):
        return [xy[0] - origin[0], xy[1] - origin[1]]
    return list(xy)


def _kinds(record):
    return {
        device["id"]: device.get("kind", "unknown")
        for device in record.get("topology", {}).get("devices", [])
    }


def _shared_nets(record, left, right):
    by_id = {device["id"]: device for device in record.get("topology", {}).get("devices", [])}
    first, second = by_id.get(left), by_id.get(right)
    if not first or not second:
        return []
    names = _net_names(record)
    left_nets = {p["net"] for p in first.get("pins", []) if p.get("net")}
    right_nets = {p["net"] for p in second.get("pins", []) if p.get("net")}
    return sorted({names.get(net, net) for net in left_nets & right_nets})


def _mirrored_pairs(record, instances, rows):
    """Adjacent same-row pairs whose orientations are left-right mirrors."""
    neighbours = set()
    for row in rows:
        members = sorted(row["members"], key=lambda name: (instances[name]["xy"][0], name))
        for left, right in zip(members, members[1:]):
            neighbours.add(frozenset((left, right)))
    names = sorted(instances)
    pairs = []
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            a, b = instances[left], instances[right]
            if frozenset((a["name"], b["name"])) not in neighbours:
                continue
            if MIRROR_VERTICAL.get(a["orient"]) != b["orient"]:
                continue
            if a["xy"][1] != b["xy"][1]:
                continue
            gap = abs(a["xy"][0] - b["xy"][0])
            if not gap:
                continue
            pairs.append(
                {
                    "a": a["name"],
                    "b": b["name"],
                    "axis": "vertical",
                    "gap": gap,
                    "same_kind": a["kind"] == b["kind"],
                    "shared_nets": _shared_nets(record, a["device"], b["device"]),
                }
            )
    pairs.sort(key=lambda item: (item["gap"], item["a"], item["b"]))
    return pairs[:MAX_PAIRS]


def _alignment(instances):
    names = sorted(instances)
    edges = []
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            a, b = instances[left], instances[right]
            for edge, index_ in (("left", 0), ("right", 1)):
                delta = a["bbox"][index_][0] - b["bbox"][index_][0]
                if abs(delta) <= EDGE_TOLERANCE:
                    edges.append({"a": a["name"], "b": b["name"], "edge": edge, "delta": delta})
                    break
    return edges[:MAX_PAIRS]


def _groups(instances, pairs, rows):
    groups = [
        {"kind": "matched_pair", "members": [pair["a"], pair["b"]]}
        for pair in pairs[:MAX_GROUPS]
    ]
    for row in rows:
        if len(groups) >= MAX_GROUPS:
            break
        if len(row["members"]) >= 3:
            groups.append({"kind": "row", "members": row["members"]})
    return groups[:MAX_GROUPS]


def relations(record):
    """Relative placement relations of one stored schematic template."""
    asset = _schematic(record)
    kinds = _kinds(record)
    instances, gaps = {}, []
    for row in asset.get("instances", []):
        xy = _relative(asset, row)
        device = row.get("device") or row.get("source_name")
        if xy is None or not device:
            gaps.append({"code": "placement_incomplete", "device": device})
            continue
        if not _box(row.get("bbox")):
            gaps.append({"code": "bounding_box_missing", "device": device})
        instances[device] = {
            "device": device,
            "name": row.get("source_name") or device,
            "kind": kinds.get(device, "unknown"),
            "xy": xy,
            "orient": row.get("orient"),
            "bbox": row.get("bbox"),
        }
    usable = {row["name"]: row for row in instances.values() if row["bbox"] is not None}
    rows, columns = _grouped(usable, 1), _grouped(usable, 0)
    pairs = _mirrored_pairs(record, usable, rows) if usable else []
    return {
        "schema": RELATIONS_SCHEMA,
        "template_ref": record.get("template_ref"),
        "unit": "source_dbu" if is_integer_space(asset) else "source_user_units",
        "dbu_per_uu": asset.get("dbu_per_uu") if is_integer_space(asset) else None,
        "instances": [instances[key] for key in sorted(instances)],
        "ports": port_relations(record),
        "device_axes": device_axes(asset),
        "rows": rows,
        "columns": columns,
        "mirrored_pairs": pairs,
        "alignment": _alignment(usable) if 1 < len(usable) <= 32 else [],
        "groups": _groups(usable, pairs, rows) if usable else [],
        "spacing": _spacing(usable) if usable else {"nearest": {}, "classes": None},
        "gaps": gaps,
        "use": "relative_relations_only; recompute_with_target_master_geometry",
    }
