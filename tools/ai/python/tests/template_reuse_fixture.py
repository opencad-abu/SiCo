"""Data-only v3 fixture; production publication remains disabled in P0."""

import copy
import sqlite3

from template_search_corpus import topology
from test_template_library_tiers import record

from cadai.template_build import publish
from cadai.template_schema import SCHEMA_V3, canonical, digest


def v3_record(optional=False):
    value = record("v3-contract-fixture")
    value["base_template_ref"] = value["template_ref"]
    value["schema_version"] = SCHEMA_V3
    value["topology"] = topology()
    value["granularity"] = "block"
    core = [d["id"] for d in value["topology"]["devices"]]
    contract = {
        "core_devices": core,
        "core_connections": [
            {"device": d["id"], "terminal": p["name"]}
            for d in value["topology"]["devices"]
            for p in d["pins"]
        ],
        "boundary_terminals": [],
        "optional_groups": [],
        "allowed_rule_refs": [],
        "placement_constraints": [],
        "routing_reference": {
            "asset": "schematic",
            "status": "unavailable",
            "anchor_ids": [],
            "segment_ids": [],
        },
    }
    if optional:
        top = value["topology"]
        drain = next(p for p in top["devices"][0]["pins"] if p["name"] == "D")
        top["devices"].append(
            {
                "id": "d2",
                "kind": "capacitor",
                "role": "device",
                "attributes": {},
                "pins": [{"name": "PLUS", "net": drain["net"]}, {"name": "MINUS", "net": "n_opt"}],
            }
        )
        top["nets"].append(
            {
                "id": "n_opt",
                "source_name": "local",
                "num_bits": 1,
                "is_global": False,
                "sig_type": "signal",
            }
        )
        top["ports"].append(
            {
                "id": "p_opt",
                "name": "OPTIONAL",
                "net": "n_opt",
                "num_bits": 1,
                "direction": "inputOutput",
            }
        )
        contract["boundary_terminals"] = [
            {
                "endpoint": {"device": "d0", "terminal": "D"},
                "allowed_device_kinds": ["capacitor"],
                "min_additional_connections": 0,
                "max_additional_connections": 2,
            }
        ]
        contract["optional_groups"] = [
            {
                "id": "load",
                "devices": ["d2"],
                "boundary_connections": [{"device": "d2", "terminal": "PLUS"}],
                "omit": {"remove_nets": ["n_opt"], "remove_ports": ["OPTIONAL"]},
            }
        ]
    value["reuse_contract"] = contract
    value["template_ref"] = "tpl_" + digest([value["base_template_ref"], contract])
    value["summary"]["template_ref"] = value["template_ref"]
    return value


def install_v3(root, value):
    """Install a fixture directly, never teach the old writer to emit v3 records."""
    old = copy.deepcopy(value)
    old["schema_version"] = "cad.circuit.template.v2"
    path = root / "catalog.sqlite3"
    publish([old], path)
    with sqlite3.connect(str(path)) as db:
        db.execute("UPDATE metadata SET value=? WHERE key='schema_version'", (SCHEMA_V3,))
        db.execute("UPDATE templates SET data_json=?", (canonical(value),))
    return path
