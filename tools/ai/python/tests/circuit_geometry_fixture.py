"""Explicit synthetic Symbol geometry; not a discovered PDK snapshot."""

from circuit_spec_fixture import sample

from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import digest


def sample_geometry():
    spec, bindings = sample()
    preview = preview_circuit(spec, bindings)
    masters = {m["id"]: m for m in bindings["masters"]}
    rows = []
    pins = {
        "P": ([0, 0.5], "up"),
        "N": ([0, -0.5], "down"),
        "IN": ([-0.5, 0], "left"),
        "OUT": ([0.5, 0], "right"),
        "VSS": ([0, -0.5], "down"),
    }
    for inst in spec["instances"]:
        master = masters[inst["master"]]
        rows.append(
            {
                "instance": inst["id"],
                "master_revision": master["revision"],
                "parameters_digest": digest(inst["parameters"]),
                "occupied_bbox": [[-0.5, -0.5], [0.5, 0.5]],
                "terminals": [
                    {
                        "name": t["name"],
                        "anchors": [
                            {"id": "pin0", "xy": pins[t["name"]][0], "escape": pins[t["name"]][1]}
                        ],
                    }
                    for t in master["terminals"]
                ],
            }
        )
    return {
        "spec": spec,
        "bindings": bindings,
        "preview_digest": preview["preview_digest"],
        "geometry": {
            "schema": "cad.circuit.geometry.v1",
            "project_ref": spec["project_ref"],
            "binding_snapshot": spec["binding_snapshot"],
            "binding_digest": preview["plan"]["binding_digest"],
            "source_ref": "geometry-fixture",
            "evidence_kind": "test_fixture",
            "coordinate_system": "master_local_schematic_uu",
            "instances": rows,
        },
        "layout": {
            "instances": {
                "dut": {"xy": [0, 0], "orientation": "R0"},
                "stim": {"xy": [-4, 0], "orientation": "R0"},
                "load": {"xy": [4, 0], "orientation": "R0"},
            },
            "ports": {},
            "anchor_choices": {},
            "grid": 0.0625,
            "stub_length": 0.25,
            "clearance": 0.125,
            # Wiring is a user decision: fixtures record the stub choice explicitly.
            "routing": {"mode": "stub", "decision": {"source": "user", "chosen": "stub"}},
        },
    }
