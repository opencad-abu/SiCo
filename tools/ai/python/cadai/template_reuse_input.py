"""Resolve explicit source names and derive contract references from captured topology."""

import copy

from .template_reuse_schema import ContractError
from .template_rules import rule_ref


def compile_contract(base, options):
    """Compile intent only; make_reusable remains the mandatory semantic authority."""
    top = base["topology"]
    devices = top["devices"]
    by_name = {}
    for device in devices:
        name = device["source_name"]
        if name in by_name:
            raise ContractError("source instance name is ambiguous", "source_instance_ambiguous",
                                name, ["reuse.core_instances", "reuse.optional_groups"])
        by_name[name] = device

    def resolve(name, field):
        if name not in by_name:
            raise ContractError("source instance is absent: " + name, "source_instance_missing",
                                name, [field])
        return by_name[name]["id"]

    core = ([d["id"] for d in devices] if options["mode"] == "whole" else
            [resolve(n, "reuse.core_instances") for n in options["core_instances"]])
    boundaries = []
    for row in options.get("boundary_terminals", []):
        name, terminal = row["endpoint"]["instance"], row["endpoint"]["terminal"]
        key = resolve(name, "reuse.boundary_terminals")
        if terminal not in {p["name"] for p in by_name[name]["pins"]}:
            raise ContractError("source terminal is absent: " + terminal,
                                "source_terminal_missing", name, ["reuse.boundary_terminals"],
                                dict(terminal=terminal))
        boundaries.append(copy.deepcopy(row) | dict(endpoint=dict(device=key, terminal=terminal)))
    groups = []
    for row in options.get("optional_groups", []):
        members = {resolve(n, "reuse.optional_groups") for n in row["instances"]}
        own = {p["net"] for d in devices if d["id"] in members for p in d["pins"]
               if p["net"] is not None}
        other = {p["net"] for d in devices if d["id"] not in members for p in d["pins"]
                 if p["net"] is not None}
        removed = {n["id"] for n in top["nets"]
                   if n["id"] in own - other and n["is_global"] is False}
        groups.append(dict(id=row["id"], devices=sorted(members), boundary_connections=[
            dict(device=d["id"], terminal=p["name"]) for d in devices if d["id"] in members
            for p in d["pins"] if p["net"] in own & other], omit=dict(
                remove_nets=sorted(removed), remove_ports=sorted(
                    p["name"] for p in top["ports"] if p["net"] in removed))))
    permissions = [("rename", options["allow_rename"]),
                   ("omit_optional_group", options.get("allow_omit_optional_groups", False)),
                   ("add_boundary_group", options.get("allow_add_boundary_group", False))]
    return dict(core_devices=core, core_connections=[
        dict(device=d["id"], terminal=p["name"]) for d in devices if d["id"] in core
        for p in d["pins"]], boundary_terminals=boundaries, optional_groups=groups,
        allowed_rule_refs=[rule_ref(name) for name, allowed in permissions if allowed],
        placement_constraints=[], routing_reference=dict(
            asset="schematic", status="unavailable", anchor_ids=[], segment_ids=[]))


def contract_issue(exc, base):
    """Translate authoritative failure identities back to caller-visible source names."""
    names = {d["id"]: d["source_name"] for d in base["topology"]["devices"]}
    # Group identifiers are caller-owned, so only device failures resolve device IDs.
    ref = exc.object_ref
    if not exc.code.startswith("source_") and exc.code not in {
        "group_id_duplicate", "group_boundary_undeclared",
                        "group_boundary_incomplete", "group_omit_nets", "group_omit_ports"}:
        ref = names.get(ref, ref)
    details = {}
    for key, value in exc.details.items():
        details[key] = value[:8] if isinstance(value, list) else value
        if isinstance(value, list) and len(value) > 8:
            details[key + "_omitted"] = len(value) - 8
    return dict(code=exc.code, object_ref=ref, message=str(exc)[:2000],
                required_fields=exc.required_fields, details=details,
                next_action="complete_saved_capture", stage="contract")
