"""Test-only DUT symbol and explicit TB inputs; not a production recipe."""

from circuit_create_fixture import fixture
from circuit_create_support import sample_master

from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import digest


def tb_fixture(bridge, cell, path):
    bridge.skill("""let((cv pm)
      cv=dbOpenCellViewByType("genericWork" "passive_dut" "symbol" "schematicSymbol" "w")
      pm=dbOpenCellViewByType("basic" "sympin" "symbolNN" "" "r")
      schCreateSymbolShape(cv "rectangle" "outline" list(0:0 2:2))
      foreach(row list(list("IN" "input" 0:0.5) list("VSS" "inputOutput" 0:1.5)
        list("OUT" "output" 2:1.0))
        schCreateSymbolPin(cv pm car(row) cadr(row) caddr(row) "R0")
        schCreateSymbolLabel(cv list(if(car(row)=="OUT" 1.875 0.125) yCoord(caddr(row)))
          "pin name" car(row) if(car(row)=="OUT" "centerRight" "centerLeft")
          "R0" "stick" 0.125 "normalLabel")
        schCreateSymbolLabel(cv caddr(row) "analog pin annotate" sprintf(nil "cdsTerm(%L)" car(row))
          "lowerCenter" "R0" "stick" 0.125 "ILLabel"))
      schCreateSymbolLabel(cv 1:2.25 "symbol shape" "passive_dut" "lowerCenter"
        "R0" "stick" 0.125 "normalLabel")
      schCreateSymbolLabel(cv 0:-0.25 "instance label" "[@instanceName]" "upperLeft"
        "R0" "stick" 0.125 "NLPLabel")
      schCreateInstBox(cv list(0:0 2:2)) dbSave(cv) dbClose(pm) dbClose(cv) t)""")
    data = {c: sample_master(bridge, c) for c in ("res", "cap", "vdc")}
    dut = sample_master(bridge, "passive_dut", "genericWork")
    args = fixture(data, cell)
    spec, bindings, geometry, layout = (args[k] for k in ("spec", "bindings", "geometry", "layout"))
    spec["kind"] = "testbench"
    spec["ports"] = []
    spec["notes"] = [
        {
            "text": "Generic TB",
            "instances": ["filter", "dut", "load"],
        }
    ]
    source = spec["instances"][0]
    source.update(
        name="VIN",
        role="stimulus",
        parameters={"vdc": "0.8"},
        connections={"PLUS": "IN", "MINUS": "VSS"},
    )
    spec["instances"][1]["role"] = "load"
    master = bindings["masters"][0]
    master.update(
        target={"library": "analogLib", "cell": "vdc", "view": "symbol"},
        revision=digest(data["vdc"]),
        parameters=[
            {"name": p[0], "type": {"int": "integer"}.get(p[1], p[1]), "editable": True}
            for p in data["vdc"][5]
        ],
    )
    geo = geometry["instances"][0]
    geo.update(
        master_revision=master["revision"],
        parameters_digest=digest(source["parameters"]),
        occupied_bbox=[[-0.125, -0.375], [0.125, 0]],
    )
    spec["instances"].append(
        {
            "id": "dut",
            "master": "dut",
            "role": "dut",
            "parameters": {},
            "connections": {"IN": "IN", "OUT": "OUT", "VSS": "VSS"},
            "unconnected": {},
        }
    )
    bindings["masters"].append(
        {
            "id": "dut",
            "target": {"library": "genericWork", "cell": "passive_dut", "view": "symbol"},
            "library_path": str(path / "library"),
            "revision": digest(dut),
            "kind": "design",
            "terminals": [{"name": t[0], "direction": t[1]} for t in dut[6]],
            "terminals_complete": True,
            "parameters": [],
            "parameters_complete": True,
            "callbacks": {"status": "none"},
        }
    )
    geometry["instances"].append(
        {
            "instance": "dut",
            "master_revision": digest(dut),
            "parameters_digest": digest({}),
            "occupied_bbox": [[0, 0], [2, 2]],
            "terminals": [
                {"name": n, "anchors": [{"id": "pin0", "xy": xy, "escape": esc}]}
                for n, xy, esc in [
                    ("IN", [0, 0.5], "left"),
                    ("VSS", [0, 1.5], "left"),
                    ("OUT", [2, 1], "right"),
                ]
            ],
        }
    )
    layout["instances"] = {
        "filter": {"xy": [-2, 1], "orientation": "R0"},
        "dut": {"xy": [0, 0], "orientation": "R0"},
        "load": {"xy": [4, 1], "orientation": "R0"},
    }
    layout["ports"] = {}
    # Compare actual sampled source anchors before using the known test-only geometry.
    for t in data["vdc"][6]:
        xy = [sum(c[k] for c in t[2][0][1]) / 2 for k in (0, 1)]
        assert xy == ([0, 0] if t[0] == "PLUS" else [0, -0.375])
    preview = preview_circuit(spec, bindings)
    geometry["binding_digest"] = preview["plan"]["binding_digest"]
    args["preview_digest"] = preview["preview_digest"]
    args.pop("geometry_plan_digest")
    plan = preview_geometry(**args)
    assert plan["plan"]["geometry_checks_passed"], plan
    args["geometry_plan_digest"] = plan["geometry_plan_digest"]
    return args
