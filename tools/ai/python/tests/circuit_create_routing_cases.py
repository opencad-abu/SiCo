"""Licensed native routing and port-geometry contracts."""

import json
import os

import pytest
from circuit_create_fixture import fixture
from circuit_create_support import sample_master, session
from test_circuit_rc_gui import mcp_call

from cadai.circuit_geometry import preview_geometry


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI acceptance")
def test_cadence_route_creates_real_routes_on_primary_pin_centres(tmp_path):
    """Default engine: the native router connects the real pin centres, not instance origins."""
    with session(tmp_path) as (bridge, server, process):
        data = {c: sample_master(bridge, c) for c in ("res", "cap")}
        task = mcp_call(
            server,
            "begin_circuit_task",
            dict(task_id="routed", presentation="background", choice_confirmed=True),
        )
        args = {
            **fixture(data, "routed", routing={"mode": "end_to_end", "ports": "route"}),
            "task_ref": task["task_ref"],
            "request_id": "routed",
        }
        prepared = mcp_call(server, "prepare_circuit_creation", args)
        assert prepared["ok"] and prepared["stage"] == "prepared", prepared
        created = mcp_call(
            server,
            "create_circuit_from_plan",
            dict(
                prepare_ref=prepared["prepare_ref"],
                geometry_plan_digest=args["geometry_plan_digest"],
                request_id="routed",
            ),
        )
        assert created["ok"] and created["saved"] and created["readback_verified"], created
        assert created["check"] == [0, 0]
        # Every signal net was handed to the Virtuoso router; VSS stays a labelled stub.
        routed = created["routing"]
        assert {row[0] for row in routed} == {"IN", "OUT"}
        assert {row[1] for row in routed} == {"routed"}, routed
        facts = {row[0]: row[1] for row in json.loads(bridge.skill(
            'let((cv segs labels nets) '
            'cv=dbOpenCellViewByType("genericWork" "routed" "schematic" "" "r") '
            'segs=setof(s cv~>shapes s~>objType=="line") '
            'labels=setof(s cv~>shapes s~>objType=="label") '
            'nets=foreach(mapcar n cv~>nets list(n~>name length(n~>shapes))) '
            'pins=foreach(mapcar i cv~>instances '
            'when(member(i~>cellName list("ipin" "opin" "iopin")) list(i~>name i~>xy i~>orient))) '
            'aiCircuitJson(list(list("segments" length(segs)) '
            'list("points" foreach(mapcar s segs s~>points)) '
            'list("labels" foreach(mapcar s labels s~>theLabel)) '
            'list("nets" nets) '
            'list("pin_instances" pins)))'
        ))}
        assert {row[0] for row in facts["nets"]} == {"IN", "OUT", "VSS"}
        assert {"IN", "OUT", "VSS"} <= set(facts["labels"])
        # The IN port pin sits at (-2,-1) R0; its connect point is the master pin
        # figure centre (-0.0625, 0) transformed by the instance transform, so the
        # route must start half an arrow short of the instance origin.
        points = [tuple(point) for group in facts["points"] for point in group]
        assert (-2.0625, -1.0) in points, facts["points"]
        assert (-2.0, -1.0) not in points, "route used the pin instance origin"
        assert ["PIN0", [-2.0, -1.0], "R0"] in facts["pin_instances"]
        # The output pin's own arrow offsets the other end by +0.0625 the same way.
        assert (5.0625, 0.0) in points, facts["points"]


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI acceptance")
def test_cadence_route_refused_edge_falls_back_to_a_labelled_stub(tmp_path):
    """The refusal path draws the endpoint's own stub from its real pin centre, once."""
    with session(tmp_path) as (bridge, server, process):
        result = json.loads(bridge.skill(
            'let((cv mr mi mp pi entry first second out) '
            'cv=dbOpenCellViewByType("genericWork" "fallback" "schematic" "schematic" "a") '
            'mr=dbOpenCellViewByType("analogLib" "res" "symbol" "" "r") '
            'mi=dbCreateInst(cv mr "RF" 0.0:0.0 "R90" 1) '
            'mp=dbOpenCellViewByType("basic" "ipin" "symbol" "" "r") '
            'pi=schCreatePin(cv mp "IN" "input" nil list(-2.0 -1.0) "R0") '
            'entry=makeTable("fallback" nil) '
            'entry[\'portInstances]=list(list("IN" pi)) '
            'schCheck(cv) '
            'first=aiCrStubTerminal(cv entry "IN" list("port" "IN") list(1 0) 0.25) '
            'second=aiCrStubTerminal(cv entry "IN" list("port" "IN") list(1 0) 0.25) '
            'out=list(list("first" first) list("second" second) '
            'list("wires" foreach(mapcar s setof(x cv~>shapes x~>objType=="line") s~>points)) '
            'list("labels" foreach(mapcar s setof(x cv~>shapes x~>objType=="label") s~>theLabel)) '
            'list("nets" foreach(mapcar n cv~>nets n~>name)) '
            'list("check" schCheck(cv))) '
            'dbClose(cv) aiCircuitJson(out))'
        ))
        facts = {row[0]: row[1] for row in result}
        assert facts["first"] and facts["second"]
        # One stub, drawn from the pin instance's own connect point (-2,-1) + (-0.0625,0).
        assert facts["wires"] == [[[-2.0625, -1.0], [-1.8125, -1.0]]], facts["wires"]
        assert facts["labels"] == ["IN"]
        assert "IN" in facts["nets"]
        assert facts["check"][0] == 0


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI acceptance")
def test_outward_port_stubs_never_rotate_the_port_pins(tmp_path):
    """Stub direction draws the wire only; the planned pin orientation is verified."""
    with session(tmp_path) as (bridge, server, process):
        data = {c: sample_master(bridge, c) for c in ("res", "cap")}
        task = mcp_call(
            server,
            "begin_circuit_task",
            dict(task_id="outward", presentation="background", choice_confirmed=True),
        )
        args = {**fixture(data, "outward"), "task_ref": task["task_ref"], "request_id": "outward"}
        # The same plan, but every port stub leaves away from the circuit.
        args["layout"] = {
            **args["layout"],
            # Stubs, not connections: this case inspects the drawn stub direction.
            "routing": {"mode": "stub"},
            "ports": {
                "IN": {"xy": [-2, -1], "escape": "left"},
                "VSS": {"xy": [-2, 1], "escape": "left"},
                "OUT": {"xy": [5, 0], "escape": "right"},
            },
        }
        geometry_args = {
            key: args[key]
            for key in ("spec", "bindings", "preview_digest", "geometry", "layout")
        }
        args["geometry_plan_digest"] = preview_geometry(**geometry_args)["geometry_plan_digest"]
        prepared = mcp_call(server, "prepare_circuit_creation", args)
        assert prepared["ok"], prepared
        created = mcp_call(
            server,
            "create_circuit_from_plan",
            dict(
                prepare_ref=prepared["prepare_ref"],
                geometry_plan_digest=args["geometry_plan_digest"],
                request_id="outward",
            ),
        )
        assert created["ok"] and created["saved"] and created["readback_verified"], created
        assert created["check"] == [0, 0]
        facts = {row[0]: row[1] for row in json.loads(bridge.skill(
            'let((cv segs) '
            'cv=dbOpenCellViewByType("genericWork" "outward" "schematic" "" "r") '
            'segs=setof(s cv~>shapes s~>objType=="line") '
            'pins=foreach(mapcar i cv~>instances '
            'when(member(i~>cellName list("ipin" "opin" "iopin")) list(i~>name i~>xy i~>orient))) '
            'aiCircuitJson(list(list("points" foreach(mapcar s segs s~>points)) '
            'list("pin_instances" pins)))'
        ))}
        # Left inputs and the right output keep R0 although their stubs point outward.
        pins = {tuple(row[1]): row[2] for row in facts["pin_instances"] if row}
        assert pins == {(-2.0, -1.0): "R0", (-2.0, 1.0): "R0", (5.0, 0.0): "R0"}, facts
        points = {tuple(point) for group in facts["points"] for point in group}
        assert (-2.0, -1.0) in points and (-2.25, -1.0) in points, facts["points"]
        assert (5.0, 0.0) in points and (5.25, 0.0) in points, facts["points"]


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed GUI acceptance")
def test_aligned_port_keeps_the_route_straight(tmp_path):
    """A port on its terminal's pin row draws one straight wire, not a one-grid jog."""
    with session(tmp_path) as (bridge, server, process):
        data = {c: sample_master(bridge, c) for c in ("res", "cap")}
        task = mcp_call(
            server,
            "begin_circuit_task",
            dict(task_id="aligned", presentation="background", choice_confirmed=True),
        )
        args = {
            **fixture(data, "aligned", routing={"mode": "end_to_end", "ports": "route"}),
            "task_ref": task["task_ref"],
            "request_id": "aligned",
        }
        # The filter and the load share their pin row at y=0; put IN on that row.
        args["layout"]["ports"]["IN"] = {"xy": [-2, 0], "escape": "right"}
        geometry_args = {key: args[key] for key in ("spec", "bindings", "preview_digest",
                                                    "geometry", "layout")}
        args["geometry_plan_digest"] = preview_geometry(**geometry_args)["geometry_plan_digest"]
        prepared = mcp_call(server, "prepare_circuit_creation", args)
        assert prepared["ok"] and prepared["stage"] == "prepared", prepared
        created = mcp_call(
            server,
            "create_circuit_from_plan",
            dict(
                prepare_ref=prepared["prepare_ref"],
                geometry_plan_digest=args["geometry_plan_digest"],
                request_id="aligned",
            ),
        )
        assert created["ok"] and created["check"] == [0, 0], created
        rows = {row[0]: row for row in created["routing"]}
        assert rows["IN"][1] == "routed" and rows["IN"][2] == 1, created["routing"]
        wires = json.loads(bridge.skill(
            'let((cv) cv=dbOpenCellViewByType("genericWork" "aligned" "schematic" "" "r") '
            'aiCircuitJson(foreach(mapcar s setof(x cv~>shapes x~>objType=="line") s~>points)))'
        ))
        assert [[-2.0625, 0.0], [0.0, 0.0]] in wires, wires
