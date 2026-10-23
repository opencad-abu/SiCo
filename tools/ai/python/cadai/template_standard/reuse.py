"""Revalidate flat contracts with the installed topology/partition rule authority."""

from ..template_reuse_input import compile_contract
from ..template_reuse_schema import validate_record
from ..template_rules import rule_ref
from ..template_schema import SCHEMA_V3
from .codec import fail
from .topology import graph


def validate_reuse(package, top, reuse):
    from .validate import keys

    if reuse["mode"] not in {"whole", "core"}:
        fail("unsupported reuse mode")
    g = graph(top)
    for d in g["devices"]:
        d["source_name"] = d["id"]
    names = []
    for key in reuse["allowed_rules"]:
        row = package["rules"][key]
        installed = rule_ref(row["name"])
        if (
            row["version"] != installed["version"]
            or row["implementation_digest"] != "sha256:" + installed["implementation_digest"]
        ):
            fail("installed rule identity differs")
        names.append(row["name"])
    options = dict(
        mode=reuse["mode"],
        allow_rename="rename" in names,
        allow_omit_optional_groups="omit_optional_group" in names,
        allow_add_boundary_group="add_boundary_group" in names,
        core_instances=reuse["core_devices"],
        optional_groups=[],
        boundary_terminals=[],
    )
    if reuse["mode"] == "whole" and (
        set(reuse["core_devices"]) != set(top["devices"])
        or reuse["optional_groups"]
        or reuse["boundaries"]
        or set(names) - {"rename"}
    ):
        fail("whole reuse must protect all devices without optional adaptations")
    for key, row in reuse["optional_groups"].items():
        keys(row, "devices")
        options["optional_groups"].append(dict(id=key, instances=row["devices"]))
    for row in reuse["boundaries"].values():
        keys(row, "connection allowed_kinds min_extra max_extra")
        endpoint = top["connections"].get(row["connection"], {})
        if "device" not in endpoint:
            fail("boundary requires a device connection")
        options["boundary_terminals"].append(
            dict(
                endpoint=dict(instance=endpoint["device"], terminal=endpoint["terminal"]),
                allowed_device_kinds=row["allowed_kinds"],
                min_additional_connections=row["min_extra"],
                max_additional_connections=row["max_extra"],
            )
        )
    contract = compile_contract(dict(topology=g), options)
    contract["placement_constraints"] = list(reuse["constraints"].values())
    validate_record(
        dict(
            schema_version=SCHEMA_V3,
            template_ref="ctpl_" + "0" * 64,
            base_template_ref="tpl_" + "1" * 64,
            granularity="block",
            topology=g,
            reuse_contract=contract,
        )
    )
