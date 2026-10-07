"""Conservative normalization of existing LX/Binder relationships.

No device reconstruction, name matching, folding or binding repair. Unsupported
hierarchical paths remain raw evidence and never contribute to mapped coverage.
"""

from __future__ import annotations

from .template_coords import instance_position, is_integer_space
from .template_schema import canonical, digest


def _members(value):
    """Documented ((object member row col) ...) only; preserve all indices."""
    if value is None:
        return [], False
    if not isinstance(value, list):
        return [], True
    result, unsupported = [], False
    for row in value:
        if isinstance(row, list) and 1 <= len(row) <= 4 and isinstance(row[0], dict):
            obj = row[0]
            indices = row[1:] + [None] * (4 - len(row))
            if (
                all(v is None or type(v) is int for v in indices)
                and obj.get("object_type") == "inst"
                and all(isinstance(obj.get(k), str) for k in ("name", "library", "cell", "view"))
            ):
                result.append(
                    {"object": obj, "member": indices[0], "row": indices[1], "column": indices[2]}
                )
                continue
        unsupported = True
    return result, unsupported


def _identity(member):
    return canonical(member)


def _matches(member, source):
    obj = member["object"]
    return all(
        obj[k] == source[v] for k, v in (("library", "lib"), ("cell", "cell"), ("view", "view"))
    )


def normalize_layout(layout, topology=None, schematic_source=None):
    physical = {i["name"]: i for i in layout["instances"]}
    logical = {d["source_name"]: d for d in (topology or {}).get("devices", [])}
    source = layout["source"]
    groups, gaps, mapped = [], [], set()
    contexts = layout.get("contexts", [])
    status = (
        contexts[0].get("binding_status", "binding_unavailable")
        if contexts
        else "binding_unavailable"
    )
    for binding in layout["bindings"]:
        if binding.get("status") != "observed" or status == "source_conflict":
            continue
        members, unsupported = _members(binding.get("objects"))
        siblings, sibling_gap = _members(binding.get("siblings"))
        if unsupported or sibling_gap:
            gaps.append({"code": "unsupported_binding_path", "instance": binding["instance"]})
        if schematic_source is None:
            gaps.append({"code": "schematic_not_captured", "instance": binding["instance"]})
            continue
        usable = [
            m for m in members if _matches(m, schematic_source) and m["object"]["name"] in logical
        ]
        # A partially understood binding is retained as evidence, never certified.
        if not usable or len(usable) != len(members) or unsupported:
            gaps.append({"code": "binding_source_unresolved", "instance": binding["instance"]})
            continue
        own = {
            "object": {
                "object_type": "inst",
                "name": binding["instance"],
                "library": source["lib"],
                "cell": source["cell"],
                "view": source["view"],
            },
            "member": None,
            "row": None,
            "column": None,
        }
        phys = [own]
        for member in siblings:
            if _matches(member, source) and member["object"]["name"] in physical:
                phys.append(member)
            else:
                gaps.append({"code": "sibling_not_captured", "instance": binding["instance"]})
        group = {
            "logical": {_identity(m): m for m in usable},
            "physical": {_identity(m): m for m in phys},
        }
        overlap = [
            g
            for g in groups
            if set(g["logical"]) & set(group["logical"])
            or set(g["physical"]) & set(group["physical"])
        ]
        for other in overlap:
            for side in ("logical", "physical"):
                group[side].update(other[side])
            groups.remove(other)
        groups.append(group)
    result = []
    for group in groups:
        logical_members = [group["logical"][k] for k in sorted(group["logical"])]
        physical_members = [group["physical"][k] for k in sorted(group["physical"])]
        # A vector bit cannot prove coverage of its whole logical vector instance.
        for member in logical_members:
            if all(member[k] is None for k in ("member", "row", "column")):
                mapped.add(member["object"]["name"])
        boxes = []
        for member in physical_members:
            inst = physical[member["object"]["name"]]
            position_key = "relative_xy" if is_integer_space(layout) else "xy"
            member["placement"] = {
                position_key: instance_position(inst),
                "orient": inst.get("orient"),
                "bbox": inst.get("bbox"),
            }
            if inst.get("bbox"):
                boxes.append(inst["bbox"])
        bbox = (
            [
                [min(b[0][i] for b in boxes) for i in (0, 1)],
                [max(b[1][i] for b in boxes) for i in (0, 1)],
            ]
            if boxes
            else None
        )
        result.append(
            {
                "id": "bg_" + digest([logical_members, physical_members]),
                "logical_members": logical_members,
                "physical_members": physical_members,
                "bbox": bbox,
                "evidence": "bndGetBoundObjects/bndGetSiblingBoundObjects",
                "terminal_mapping": "unresolved; permuted_terms_is_not_a_pair_mapping",
            }
        )
    if groups:
        status = (
            "observed_partial"
            if gaps or len(mapped) != len(logical)
            else "observed_direct_instances"
        )
    unmatched = [
        {"device": d["id"], "source_name": name, "reason": "missing_unknown" if groups else status}
        for name, d in logical.items()
        if name not in mapped
    ]
    layout["binding_groups"] = result
    layout["unmapped_devices"] = unmatched
    layout["binding_gaps"] = gaps
    # Values are source evidence only; no PDK-specific nf/fold interpretation is invented.
    layout["multiplicity_properties"] = [
        p
        for p in layout["instance_properties"]
        if p["name"].lower()
        in {"nf", "fingers", "finger", "m", "s", "fold", "folds", "mult", "multiplicity"}
    ]
    layout["coverage"].update(
        {
            "status": "partial",
            "physical_instances": len(physical),
            "binding_groups": len(result),
            "logical_devices": len(logical) if topology is not None else None,
            "logical_devices_mapped": len(mapped) if groups else None,
            "unmapped_devices": len(unmatched) if groups else None,
            "missing_flattened": None,
            "binding_status": status,
            "flatten_policy": "skip_missing_instances_no_reconstruction",
            "flatten_reason": (
                "no_independent_flatten_evidence; never_inferred_from_missing_binding"
            ),
        }
    )
    return layout
