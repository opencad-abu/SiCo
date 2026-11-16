"""Two deliberately incompatible synthetic project interfaces; no live PDK claims."""

import copy


def sample(project="a"):
    ports = ["P", "N"] if project == "a" else ["positive", "negative"]
    param = "value" if project == "a" else "resistance"
    project_ref, snapshot = "project:" + project, "snapshot:" + project
    terminals = [{"name": p, "direction": "inputOutput"} for p in ports]
    masters = [
        {
            "id": "selected_resistor",
            "target": {"library": "devices_" + project, "cell": "res_" + project, "view": "symbol"},
            "library_path": "/project/" + project,
            "revision": "rev1",
            "kind": "device",
            "terminals": terminals,
            "terminals_complete": True,
            "parameters_complete": True,
            "parameters": [{"name": param, "type": "string", "editable": True, "unit": "ohm"}],
            "callbacks": {"status": "none"},
        },
        {
            "id": "selected_source",
            "target": {
                "library": "sources_" + project,
                "cell": "source_" + project,
                "view": "symbol",
            },
            "library_path": "/sources/" + project,
            "revision": "rev2",
            "kind": "device",
            "terminals": terminals,
            "terminals_complete": True,
            "parameters_complete": True,
            "parameters": [{"name": "amplitude", "type": "string", "editable": True}],
            "callbacks": {"status": "none"},
        },
        {
            "id": "selected_dut",
            "target": {
                "library": "work_" + project,
                "cell": "filter" if project == "a" else "comparator",
                "view": "symbol",
            },
            "library_path": "/design/" + project,
            "revision": "rev3",
            "kind": "design",
            "terminals": [
                {"name": "IN", "direction": "input"},
                {"name": "OUT", "direction": "output"},
                {"name": "VSS", "direction": "inputOutput"},
            ],
            "terminals_complete": True,
            "parameters_complete": True,
            "parameters": [],
            "callbacks": {"status": "none"},
        },
    ]
    spec = {
        "schema": "cad.circuit.spec.v1",
        "project_ref": project_ref,
        "binding_snapshot": snapshot,
        "kind": "testbench",
        "global_net_authorization": "Test explicitly requests global nets to cover the optional global connection path",
        "target": {"library": "work_" + project, "cell": "my_tb", "view": "schematic"},
        "instances": [
            {
                "id": "load",
                "name": "Rout",
                "master": "selected_resistor",
                "role": "load",
                "parameters": {param: "RLOAD"},
                "connections": dict(zip(ports, ["OUT", "gnd!"])),
                "unconnected": {},
            },
            {
                "id": "stim",
                "name": "Vin",
                "master": "selected_source",
                "role": "stimulus",
                "parameters": {"amplitude": "VIN"},
                "connections": dict(zip(ports, ["IN", "gnd!"])),
                "unconnected": {},
            },
            {
                "id": "dut",
                "master": "selected_dut",
                "role": "dut",
                "parameters": {},
                "connections": {"IN": "IN", "OUT": "OUT", "VSS": "gnd!"},
                "unconnected": {},
            },
        ],
        "nets": [
            {"name": "IN", "scope": "local"},
            {"name": "OUT", "scope": "local"},
            {"name": "gnd!", "scope": "global"},
        ],
        "ports": [],
        "notes": [{"text": "负载区", "instances": ["load"]}],
    }
    bindings = {
        "schema": "cad.circuit.bindings.v1",
        "project_ref": project_ref,
        "snapshot_ref": snapshot,
        "source_ref": "synthetic-fixture-only",
        "evidence_kind": "test_fixture",
        "masters": masters,
    }
    return copy.deepcopy(spec), copy.deepcopy(bindings)
