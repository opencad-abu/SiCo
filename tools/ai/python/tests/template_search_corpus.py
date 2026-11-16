"""Synthetic, topology-labeled corpus. Families, not variants, define the split.

Labels establish saved structural equivalence only. No expert analog-performance
or live geometry qualification is claimed for these generated circuits.
"""

from copy import deepcopy

from cadai.template_schema import RULE_VERSION, digest

VERSION = "template-exact-corpus.v1"
# Changes to labeled inputs require an explicit version/digest review.
DATASET_DIGEST = "5532268790f798f2d274895fd242f7aa170be3f625def59e3b80b3fad0e93145"
FAMILIES = {
    "inverter": [
        ("nmos", {"G": "IN", "D": "OUT", "S": "VSS", "B": "VSS"}),
        ("pmos", {"G": "IN", "D": "OUT", "S": "VDD", "B": "VDD"}),
    ],
    "mirror": [
        ("nmos", {"G": "IN", "D": "IN", "S": "VSS", "B": "VSS"}),
        ("nmos", {"G": "IN", "D": "OUT", "S": "VSS", "B": "VSS"}),
    ],
    "rc_filter": [
        ("resistor", {"PLUS": "IN", "MINUS": "OUT"}),
        ("capacitor", {"PLUS": "OUT", "MINUS": "VSS"}),
    ],
    "differential_pair": [
        ("nmos", {"G": "IN", "D": "OUT", "S": "TAIL", "B": "VSS"}),
        ("nmos", {"G": "IN_NEG", "D": "OUT_NEG", "S": "TAIL", "B": "VSS"}),
        ("current_source", {"PLUS": "TAIL", "MINUS": "VSS"}),
    ],
    "diode_load": [
        ("resistor", {"PLUS": "IN", "MINUS": "OUT"}),
        ("diode", {"PLUS": "OUT", "MINUS": "VSS"}),
    ],
    "common_source": [
        ("nmos", {"G": "IN", "D": "OUT", "S": "VSS", "B": "VSS"}),
        ("resistor", {"PLUS": "VDD", "MINUS": "OUT"}),
        ("capacitor", {"PLUS": "OUT", "MINUS": "VSS"}),
    ],
}
DEVELOPMENT = {"inverter", "mirror", "rc_filter"}
SCENARIOS = {
    "identity": "needs_mapping",
    "renamed_ids": "needs_mapping",
    "explicit_port_map": "needs_mapping",
    "size_r90": "needs_mapping",
    "size_my": "needs_mapping",
    "wrong_connection": "no_match",
    "wrong_terminal": "no_match",
    "wrong_kind": "no_match",
    "wrong_global": "no_match",
    "wrong_direction": "no_match",
    "unknown_inventory": "inconclusive",
    "unexpanded_bus": "inconclusive",
}


def topology(family="inverter"):
    circuit = FAMILIES[family]
    names = sorted({net for _, pins in circuit for net in pins.values()})
    ids = {name: "n" + str(i) for i, name in enumerate(names)}
    devices = [
        {
            "id": "d" + str(i),
            "source_name": "M" + str(i),
            "kind": kind,
            "role": "device",
            "attributes": {},
            "pins": [{"name": name, "net": ids[net]} for name, net in sorted(pins.items())],
        }
        for i, (kind, pins) in enumerate(circuit)
    ]
    nets = [
        {
            "id": ids[name],
            "source_name": name + ("!" if name.startswith("VS") else ""),
            "num_bits": 1,
            "is_global": name.startswith("VS"),
            "sig_type": "signal",
        }
        for name in names
    ]
    ports = [
        {
            "id": "p" + str(i),
            "name": name,
            "net": ids[name],
            "num_bits": 1,
            "direction": "input" if name.startswith("IN") else "output",
        }
        for i, name in enumerate(n for n in names if n.startswith(("IN", "OUT")))
    ]
    return {
        "level": "direct",
        "devices": devices,
        "nets": nets,
        "ports": ports,
        "gaps": [],
        "rule_version": RULE_VERSION,
        "net_global_semantics": "uniform_boolean_v1",
    }


def pool_item(top=None, number=None, origins=None):
    top = deepcopy(top if top is not None else topology())
    return {
        "template_ref": "tpl_" + (f"{number:064x}" if number is not None else digest(top)),
        "topology": top,
        "origins": origins or ["private"],
    }


def cases():
    values = []
    for family in FAMILIES:
        reference = pool_item(topology(family))
        for scenario, status in SCENARIOS.items():
            target = topology(family)
            expected_map = {
                "devices": {d["id"]: d["id"] for d in target["devices"]},
                "nets": {n["id"]: n["id"] for n in target["nets"]},
                "ports": {p["name"]: p["name"] for p in target["ports"]},
            }
            port_maps = {}
            if scenario == "renamed_ids":
                for device in target["devices"]:
                    expected_map["devices"][device["id"]] = "target_" + device["id"]
                    device["id"] = "target_" + device["id"]
                    for pin in device["pins"]:
                        pin["net"] = "target_" + pin["net"]
                for net in target["nets"]:
                    expected_map["nets"][net["id"]] = "target_" + net["id"]
                    net["id"] = "target_" + net["id"]
                    if not net["is_global"]:
                        net["source_name"] = "renamed_" + net["source_name"]
                for port in target["ports"]:
                    port["net"] = "target_" + port["net"]
            elif scenario == "explicit_port_map":
                port = target["ports"][0]
                expected_map["ports"][port["name"]] = "RENAMED"
                port_maps = {reference["template_ref"]: {port["name"]: "RENAMED"}}
                port["name"] = "RENAMED"
            elif scenario.startswith("size_"):
                for i, device in enumerate(target["devices"]):
                    device["parameters"] = {"w": str(i + 3) + "u", "l": "0.5u"}
                    device["source_orientation"] = scenario[5:].upper()
                    device["source_bbox"] = [[-i - 2, -3], [i + 2, 7]]
            elif scenario == "wrong_connection":
                pin = target["devices"][0]["pins"][0]
                pin["net"] = next(n["id"] for n in target["nets"] if n["id"] != pin["net"])
            elif scenario == "wrong_terminal":
                target["devices"][0]["pins"][0]["name"] = "WRONG_TERMINAL"
            elif scenario == "wrong_kind":
                target["devices"][0]["kind"] = "inductor"
            elif scenario == "wrong_global":
                next(n for n in target["nets"] if n["is_global"])["source_name"] = "OTHER!"
            elif scenario == "wrong_direction":
                target["ports"][0]["direction"] = "output"
            elif scenario == "unknown_inventory":
                target["gaps"].append({"code": "terminal_inventory_unverified"})
            elif scenario == "unexpanded_bus":
                target["nets"][0]["num_bits"] = 2
            values.append(
                {
                    "id": family + "/" + scenario,
                    "family": family,
                    "split": "development" if family in DEVELOPMENT else "acceptance",
                    "label_source": "synthetic_structural_invariant",
                    "target": target,
                    "candidates": [deepcopy(reference)],
                    "port_maps": port_maps,
                    "expected_status": status,
                    "expected_mapping": expected_map if status == "needs_mapping" else None,
                    "allowed_adaptations": [],
                }
            )
    return values
