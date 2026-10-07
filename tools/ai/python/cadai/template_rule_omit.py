"""Pure removal of one declared optional group, with dangling-reference checks."""

import copy

from .template_schema import TemplateError


def omit_group(record, groups):
    top = copy.deepcopy(record["topology"])
    removed = {"devices": [], "nets": [], "ports": []}
    if not groups:
        return top, removed
    declared = {g["id"]: g for g in record["reuse_contract"]["optional_groups"]}
    if len(groups) != 1 or groups[0] not in declared:
        raise TemplateError("needs_adaptation: only one declared optional group may be omitted")
    group = declared[groups[0]]
    removed = {"devices": sorted(group["devices"]),
               "nets": sorted(group["omit"]["remove_nets"]),
               "ports": sorted(group["omit"]["remove_ports"])}
    for key, identity in (("devices", "id"), ("nets", "id"), ("ports", "name")):
        top[key] = [row for row in top[key] if row[identity] not in removed[key]]
    remaining = {n["id"] for n in top["nets"]}
    connected = [p["net"] for d in top["devices"] for p in d["pins"]]
    connected += [p["net"] for p in top["ports"]]
    if any(n is not None and n not in remaining for n in connected):
        raise TemplateError("needs_adaptation: omission leaves a dangling connection")
    return top, removed
