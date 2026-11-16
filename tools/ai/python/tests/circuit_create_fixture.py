"""Acceptance input assembled from exact selected live masters; never shipped as defaults."""

from circuit_create_support import IC_LIBS
from test_circuit_rc_gui import mcp_call

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import digest


def fixture(data, cell, worklib="genericWork", routing=None):
    # Passive two-device network gives independent port/connection and numeric-property checks.
    bindings = {
        "schema": "cad.circuit.bindings.v1",
        "project_ref": "project:test",
        "snapshot_ref": "snapshot:test",
        "source_ref": "test-live-sampling",
        "evidence_kind": "session_capture",
        "masters": [],
    }
    spec = {
        "schema": "cad.circuit.spec.v1",
        "project_ref": bindings["project_ref"],
        "binding_snapshot": bindings["snapshot_ref"],
        "kind": "circuit",
        "target": {"library": worklib, "cell": cell, "view": "schematic"},
        "instances": [],
        "nets": [{"name": n, "scope": "local"} for n in ("IN", "OUT", "VSS")],
        "ports": [
            {"name": "IN", "direction": "input", "net": "IN"},
            {"name": "OUT", "direction": "output", "net": "OUT"},
            {"name": "VSS", "direction": "inputOutput", "net": "VSS"},
        ],
        "notes": [{"text": "Passive network | generic creation", "instances": ["filter", "load"]}],
    }
    geos = []
    for key, name, device, params, conns in [
        ("filter", "RF", "res", {"r": "2K"}, {"PLUS": "IN", "MINUS": "OUT"}),
        ("load", "CL", "cap", {"c": "3p", "polyCoef": 0}, {"PLUS": "OUT", "MINUS": "VSS"}),
    ]:
        captured = data[device]
        bindings["masters"].append(
            {
                "id": key,
                "target": {"library": "analogLib", "cell": device, "view": "symbol"},
                "library_path": str(IC_LIBS / "artist/analogLib"),
                "revision": digest(captured),
                "kind": "device",
                "terminals": [{"name": t[0], "direction": t[1]} for t in captured[6]],
                "terminals_complete": True,
                "parameters": [
                    {
                        "name": p[0],
                        "type": {"int": "integer", "cyclic": "string"}.get(p[1], p[1]),
                        "editable": p[1] != "cyclic",
                    }
                    for p in captured[5]
                ],
                "parameters_complete": True,
                "callbacks": {"status": "none"},
            }
        )
        spec["instances"].append(
            {
                "id": key,
                "name": name,
                "master": key,
                "role": "device",
                "parameters": params,
                "connections": conns,
                "unconnected": {},
            }
        )
        terms = []
        for term in captured[6]:
            xy = [(term[2][0][1][0][k] + term[2][0][1][1][k]) / 2 for k in (0, 1)]
            terms.append(
                {
                    "name": term[0],
                    "anchors": [
                        {"id": "pin0", "xy": xy, "escape": "up" if term[0] == "PLUS" else "down"}
                    ],
                }
            )
        geos.append(
            {
                "instance": key,
                "master_revision": digest(captured),
                "parameters_digest": digest(params),
                "occupied_bbox": [[-0.125, -0.375], [0.125, 0]],
                "terminals": terms,
            }
        )
    preview = preview_circuit(spec, bindings)
    geometry = {
        "schema": "cad.circuit.geometry.v1",
        "project_ref": bindings["project_ref"],
        "binding_snapshot": bindings["snapshot_ref"],
        "binding_digest": preview["plan"]["binding_digest"],
        "source_ref": "test-live-geometry",
        "evidence_kind": "session_capture",
        "coordinate_system": "master_local_schematic_uu",
        "instances": geos,
    }
    layout = {
        "instances": {
            "filter": {"xy": [0, 0], "orientation": "R90"},
            "load": {"xy": [3, 0], "orientation": "R0"},
        },
        "ports": {
            "IN": {"xy": [-2, -1], "escape": "right"},
            "VSS": {"xy": [-2, 1], "escape": "right"},
            "OUT": {"xy": [5, 0], "escape": "left"},
        },
        "anchor_choices": {},
        "grid": 0.0625,
        "stub_length": 0.25,
        "clearance": 0.125,
    }
    layout["routing"] = routing or {"mode": "stub", "decision": {"source": "user", "chosen": "stub"}}
    args = {
        "spec": spec,
        "bindings": bindings,
        "preview_digest": preview["preview_digest"],
        "geometry": geometry,
        "layout": layout,
    }
    geo = preview_geometry(**args)
    assert geo["plan"]["geometry_checks_passed"], geo
    return {**args, "geometry_plan_digest": geo["geometry_plan_digest"]}


def prepare(server, data, cell, mode="foreground"):
    args = {**fixture(data, cell), "task_ref": "task:" + mode, "request_id": cell}
    result = mcp_call(server, "prepare_circuit_creation", args)
    assert result["ok"], result
    return dict(
        prepare_ref=result["prepare_ref"],
        geometry_plan_digest=args["geometry_plan_digest"],
        request_id=cell,
    )
