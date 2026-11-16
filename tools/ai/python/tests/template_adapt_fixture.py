"""PDK-neutral P2 fixtures with explicit type evidence and complete target circuits."""

import copy

from template_search_corpus import topology
from test_template_library_tiers import record

from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import digest
from cadai.template_build import summary
from cadai.template_graph import fingerprint
from cadai.template_reuse_build import make_reusable, publish_reusable
from cadai.template_rules import rule_ref


def reusable(family="differential_pair", optional=False):
    base = record("p2-" + family)
    top = topology(family)
    for net in top["nets"]:
        net["is_global"] = False
        net["source_name"] = net["source_name"].rstrip("!")
    for device in top["devices"]:
        device["master"] = {"library": "testDevices", "cell": device["kind"], "view": "symbol"}
    core = [d["id"] for d in top["devices"]]
    boundaries = []
    for net in top["nets"]:
        if net["source_name"] in {"OUT", "VSS"}:
            owner, pin = next(
                (d, p) for d in top["devices"] for p in d["pins"] if p["net"] == net["id"]
            )
            boundaries.append(
                {
                    "endpoint": {"device": owner["id"], "terminal": pin["name"]},
                    "allowed_device_kinds": ["capacitor", "resistor"],
                    "min_additional_connections": 0,
                    "max_additional_connections": max(4, int(optional)),
                }
            )
    contract = {
        "core_devices": core,
        "core_connections": [
            {"device": d["id"], "terminal": p["name"]} for d in top["devices"] for p in d["pins"]
        ],
        "boundary_terminals": boundaries,
        "optional_groups": [],
        "allowed_rule_refs": [
            rule_ref(n) for n in ("rename", "add_boundary_group", "omit_optional_group")
        ],
        "placement_constraints": [],
        "routing_reference": {
            "asset": "schematic",
            "status": "unavailable",
            "anchor_ids": [],
            "segment_ids": [],
        },
    }
    if optional:
        out = next(n["id"] for n in top["nets"] if n["source_name"] == "OUT")
        key = "d" + str(len(core))
        top["devices"].append(
            {
                "id": key,
                "source_name": "CX",
                "kind": "capacitor",
                "role": "device",
                "attributes": {},
                "master": {"library": "testDevices", "cell": "capacitor", "view": "symbol"},
                "pins": [{"name": "PLUS", "net": out}, {"name": "MINUS", "net": "n_extra"}],
            }
        )
        top["nets"].append(
            {
                "id": "n_extra",
                "source_name": "BIAS",
                "num_bits": 1,
                "is_global": False,
                "sig_type": "signal",
            }
        )
        top["ports"].append(
            {"id": "p_extra", "name": "BIAS", "net": "n_extra", "direction": "input", "num_bits": 1}
        )
        contract["optional_groups"] = [
            {
                "id": "load",
                "devices": [key],
                "boundary_connections": [{"device": key, "terminal": "PLUS"}],
                "omit": {"remove_nets": ["n_extra"], "remove_ports": ["BIAS"]},
            }
        ]
        for number in range(1, int(optional)):
            device = copy.deepcopy(top["devices"][-1])
            device.update(id="d" + str(len(core) + number), source_name="CX" + str(number))
            device["pins"][1]["net"] = "n_extra" + str(number)
            top["devices"].append(device)
            top["nets"].append({**top["nets"][-1], "id": device["pins"][1]["net"],
                                "source_name": "BIAS" + str(number)})
            top["ports"].append({**top["ports"][-1], "id": "p_extra" + str(number),
                                 "name": "BIAS" + str(number), "net": device["pins"][1]["net"]})
            contract["optional_groups"].append({
                "id": "load" + str(number), "devices": [device["id"]],
                "boundary_connections": [{"device": device["id"], "terminal": "PLUS"}],
                "omit": {"remove_nets": [device["pins"][1]["net"]],
                         "remove_ports": ["BIAS" + str(number)]},
            })
    top["counts"] = {
        "devices": len(top["devices"]),
        "nets": len(top["nets"]),
        "ports": len(top["ports"]),
        "device_kinds": {},
    }
    top["fingerprint"] = fingerprint(top)
    base["topology"] = top
    masters = sorted({
        (d["master"]["library"], d["master"]["cell"], d["master"]["view"])
        for d in top["devices"]
    })
    views = [{
        "target": [base["source"]["lib"], base["source"]["cell"], base["source"]["view"]],
        "oa_view_type": "schematic",
        "source_view": "source",
        "parameterized": False,
        "hierarchy_count": 0,
        "instance_count": len(top["devices"]),
        "mosaic_count": 0,
        "terminal_expressions": [],
        "signal_expressions": [],
    }]
    views.extend({
        "target": list(master),
        "oa_view_type": "schematicSymbol",
        "source_view": "master",
        "parameterized": False,
        "hierarchy_count": 0,
        "instance_count": 0,
        "mosaic_count": 0,
        "terminal_expressions": [],
        "signal_expressions": [],
    } for master in masters)
    base["source"]["electrical_observation"] = {
        "schema": "cad.template.electrical-observation.v1", "views": views
    }
    base["assets"] = {
        "schematic": {
            "coordinate_space": "source_dbu_integer_relative",
            "dbu_per_uu": 16,
            "instances": [
                {
                    "device": d["id"],
                    "source_name": d["source_name"],
                    "relative_xy": [i * 128, 0],
                    "orient": "R0",
                    "bbox": [[i * 128, 0], [i * 128 + 16, 16]],
                }
                for i, d in enumerate(top["devices"])
            ],
            "shapes": [],
            "properties": [],
        }
    }
    base["source"]["dynamic_text_policy"] = "ignored_for_electrical_graph"
    base["assets"]["schematic"]["wire_reference"] = {
        "schema": "cad.template.wire-reference.v1",
        "dependency_digest": digest({"fixture": "p2", "family": family}),
        "geometry": [
            {
                "device": d["id"], "library_path": "/test/devices",
                "revision": "0" * 64,
                "occupied_bbox": [[0, 0], [1, max(1, len(d["pins"]))]],
                "terminals": [
                    {"name": p["name"], "anchors": [{"id": "a0", "xy": [0, i + 0.5]}]}
                    for i, p in enumerate(sorted(d["pins"], key=lambda p: p["name"]))
                ],
            }
            for d in top["devices"]
        ],
        "ports": [
            {"port": p["name"], "id": "p0", "xy": [2, i]}
            for i, p in enumerate(top["ports"])
        ],
        "gaps": [],
    }
    base["summary"] = summary(base)
    classifications = {
        d["id"]: {
            "kind": d["kind"],
            "attributes": {},
            "source_ref": "fixture:type",
            "revision": "fixture:v1",
        }
        for d in top["devices"]
    }
    return make_reusable(base, contract, classifications)


def target_case(reference, *, extra=False, omit=False):
    from cadai.template_omit_selection import omit_selected

    omitted = list(omit) if isinstance(omit, (list, tuple)) else ["load"] if omit else []
    top, _ = omit_selected(reference, omitted)
    target = copy.deepcopy(top)
    names = {n["id"]: n["source_name"] for n in target["nets"]}
    mapping = {
        "device_map": {d["id"]: "I" + d["id"] for d in top["devices"]},
        "terminal_map": {
            d["id"]: {p["name"]: p["name"] for p in d["pins"]} for d in top["devices"]
        },
        "net_map": names,
        "port_map": {p["name"]: p["name"] for p in top["ports"]},
        "omitted_groups": omitted,
    }
    if extra:
        target["devices"].append(
            {
                "id": "extra",
                "source_name": "CX",
                "kind": "capacitor",
                "role": "device",
                "attributes": {},
                "pins": [
                    {"name": "PLUS", "net": next(k for k, v in names.items() if v == "OUT")},
                    {"name": "MINUS", "net": "n_bias"},
                ],
            }
        )
        target["nets"].append(
            {
                "id": "n_bias",
                "source_name": "BIAS",
                "num_bits": 1,
                "is_global": False,
                "sig_type": "signal",
            }
        )
        target["ports"].append(
            {"id": "p_bias", "name": "BIAS", "net": "n_bias", "num_bits": 1, "direction": "input"}
        )
        names = {**names, "n_bias": "BIAS"}
    bindings = {
        "schema": "cad.circuit.bindings.v1",
        "project_ref": "project:p2",
        "snapshot_ref": "snapshot:p2",
        "source_ref": "fixture:p2",
        "evidence_kind": "test_fixture",
        "masters": [],
    }
    spec = {
        "schema": "cad.circuit.spec.v1",
        "project_ref": "project:p2",
        "binding_snapshot": "snapshot:p2",
        "kind": "circuit",
        "target": {"library": "work", "cell": "p2", "view": "schematic"},
        "instances": [],
        "nets": [{"name": names[n["id"]], "scope": "local"} for n in target["nets"]],
        "ports": [
            {"name": p["name"], "net": names[p["net"]], "direction": p["direction"]}
            for p in target["ports"]
        ],
    }
    for d in target["devices"]:
        key = mapping["device_map"].get(d["id"], "EXTRA")
        bindings["masters"].append(
            {
                "id": key,
                "target": {"library": "testDevices", "cell": key, "view": "symbol"},
                "library_path": "/test/devices",
                "revision": "fixture:1",
                "kind": "device",
                "terminals": [{"name": p["name"], "direction": "inputOutput"} for p in d["pins"]],
                "terminals_complete": True,
                "parameters": [],
                "parameters_complete": True,
                "callbacks": {"status": "none"},
                "classification": {
                    "kind": d["kind"],
                    "attributes": {},
                    "source_ref": "fixture:type",
                    "revision": "fixture:v1",
                },
            }
        )
        spec["instances"].append(
            {
                "id": key,
                "name": key,
                "master": key,
                "role": "device",
                "parameters": {},
                "connections": {p["name"]: names[p["net"]] for p in d["pins"]},
                "unconnected": {},
            }
        )
    spec = preview_circuit(spec, bindings)["plan"]["spec"]
    return {
        "request_schema": "cad.template.adapt-request.v2",
        "template_ref": reference["template_ref"],
        "spec": spec,
        "bindings": bindings,
        "mapping": mapping,
        "allowed_rule_refs": copy.deepcopy(reference["reuse_contract"]["allowed_rule_refs"]),
    }


def install(workspace, reference):
    from sicostate import project_directory

    workspace.mkdir(parents=True, exist_ok=True)
    root = project_directory(workspace, "ai/reference/circuit-templates/catalogs", create=True)
    publish_reusable([reference], root / (reference["template_ref"] + ".sqlite3"))


def geometry_request(prepared):
    spec, bindings = prepared["spec"], prepared["plan"]["bindings"]
    rows, positions = [], {}
    for index, instance in enumerate(spec["instances"]):
        key = instance["id"]
        positions[key] = {"xy": [index * 8, 0], "orientation": "R0"}
        terminals = sorted(instance["connections"])
        height = max(1, len(terminals))
        rows.append(
            {
                "instance": key,
                "master_revision": "fixture:1",
                "parameters_digest": digest({}),
                "occupied_bbox": [[0, 0], [1, height]],
                "terminals": [
                    {"name": t, "anchors": [{"id": "p0", "xy": [0, i + 0.5], "escape": "left"}]}
                    for i, t in enumerate(terminals)
                ],
            }
        )
    layout = {
        "instances": positions,
        "ports": {
            p["name"]: {
                "xy": [len(positions) * 8 if p["direction"] == "output" else -4, -4 - i],
                "escape": "left" if p["direction"] == "output" else "right",
            }
            for i, p in enumerate(spec["ports"])
        },
        "anchor_choices": {},
        "grid": 0.0625,
        "stub_length": 0.25,
        "clearance": 0,
        "routing": {"mode": "stub"},
    }
    geometry = {
        "schema": "cad.circuit.geometry.v1",
        "project_ref": "project:p2",
        "binding_snapshot": "snapshot:p2",
        "binding_digest": digest(bindings),
        "source_ref": "fixture:p2",
        "evidence_kind": "test_fixture",
        "coordinate_system": "master_local_schematic_uu",
        "instances": rows,
    }
    return {
        "spec": spec,
        "bindings": bindings,
        "template_use": prepared["template_use"],
        "preview_digest": prepared["preview_digest"],
        "geometry": geometry,
        "layout": layout,
    }
