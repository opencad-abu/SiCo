"""A design-unit TB consumes its saved symbol, never a copied schematic."""

import json
import os

import pytest
from circuit_create_support import AI_ROOT, session
from template_symbol_fixture import scratch_schematic
from test_circuit_rc_gui import mcp_call
from test_template_symbol import example_record

from cadai.circuit_create import payload, skill_literal
from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit
from cadai.template_build import make_template, publish
from cadai.template_capture import read_capture


def tb_request(binding):
    master = binding["master"]
    ports = master["terminals"]
    bindings = dict(
        schema="cad.circuit.bindings.v1",
        project_ref="project:tb",
        snapshot_ref="snapshot:tb",
        source_ref=binding["source_ref"],
        evidence_kind="session_capture",
        masters=[master],
    )
    spec = dict(
        schema="cad.circuit.spec.v1",
        project_ref="project:tb",
        binding_snapshot="snapshot:tb",
        kind="testbench",
        target=dict(library="SicoTest", cell="dut_tb", view="schematic"),
        instances=[
            dict(
                id="dut",
                master="dut",
                role="dut",
                parameters={},
                unconnected={},
                connections={p["name"]: p["name"] for p in ports},
            )
        ],
        nets=[dict(name=p["name"], scope="local") for p in ports],
        ports=[dict(**p, net=p["name"]) for p in ports],
    )
    preview = preview_circuit(spec, bindings)
    geometry = dict(
        schema="cad.circuit.geometry.v1",
        project_ref="project:tb",
        binding_snapshot="snapshot:tb",
        binding_digest=preview["plan"]["binding_digest"],
        source_ref=binding["source_ref"],
        evidence_kind="session_capture",
        coordinate_system="master_local_schematic_uu",
        instances=[binding["geometry"]],
    )
    placements = {}
    for term in binding["geometry"]["terminals"]:
        anchor = term["anchors"][0]
        left = anchor["escape"] == "left"
        placements[term["name"]] = dict(
            xy=[anchor["xy"][0] + (-2 if left else 2), anchor["xy"][1]],
            escape="right" if left else "left",
        )
    layout = dict(
        instances=dict(dut=dict(xy=[0, 0], orientation="R0")),
        ports=placements,
        anchor_choices={},
        grid=0.0625,
        stub_length=0.25,
        clearance=0.125,
        routing=dict(mode="stub", decision=dict(source="user", chosen="stub")),
    )
    args = dict(
        spec=spec,
        bindings=bindings,
        preview_digest=preview["preview_digest"],
        geometry=geometry,
        layout=layout,
    )
    args["geometry_plan_digest"] = preview_geometry(**args)["geometry_plan_digest"]
    return dict(**args, task_ref="task:tb", request_id="tb")


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed TB acceptance")
def test_missing_symbol_created_on_source_design_before_tb(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(catalog))
    with session(tmp_path, worklib="SicoTest") as (bridge, server, _):
        for name in (
            "AI_test_workspace.il",
            "AI_templates.il",
            "AI_circuit_symbol.il",
            "AI_template_symbol_geometry.il",
            "AI_template_symbol.il",
            "AI_template_symbol_binding.il",
        ):
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / name)) + ")")
        bridge.skill('unless(ddGetObj("SicoTest") error("missing private test library"))')
        assert bridge.skill('ddGetObj("SicoTest" "dut")') == "nil"
        assert bridge.skill('ddGetObj("SicoTest" "dut_tb")') == "nil"
        scratch_schematic(bridge, "SicoTest", "dut", example_record())
        assert bridge.skill('ddGetObj("SicoTest" "dut" "symbol")') == "nil"
        snapshot = (
            'let((cv result) cv=dbOpenCellViewByType("SicoTest" "dut" "schematic" "" "r") '
            "result=aiCircuitJson(aiCrSnapshot(cv)) dbClose(cv) result)"
        )
        original = bridge.skill(snapshot)
        mcp_call(
            server,
            "begin_circuit_task",
            dict(task_id="tb", presentation="background", choice_confirmed=True),
        )
        captured = tmp_path / "dut.jsonl"
        bridge.skill(
            'aiTplCapture("SicoTest" "dut" "schematic" "symbol" "layout" '
            + json.dumps(str(captured))
            + ' list("schematic"))'
        )
        record = make_template(read_capture(captured))
        publish([record], catalog / "catalog.sqlite3")
        args = dict(
            template_ref=record["template_ref"],
            task_ref="task:tb",
            request_id="symbol",
            library="SicoTest",
            cell="dut",
        )
        # Missing symbols are a prerequisite failure, never an excuse to copy schematic.
        row = [
            "I_dut_0",
            ["SicoTest", "dut", "symbol"],
            str(tmp_path / "library"),
            [0, 0],
            "R0",
            None,
            None,
            None,
            [[0, 0], [1, 1]],
            None,
            "design",
            None,
        ]
        native_payload = [["SicoTest", "dut_tb", "schematic"], [row], None, None, None]
        missing = json.loads(
            bridge.skill(
                'aiCreatePrepare("task:tb" "missing" "digest" '
                + skill_literal(native_payload)
                + ")"
            )
        )
        assert not missing["ok"] and "selected symbol missing" in missing["message"]
        assert bridge.skill('ddGetObj("SicoTest" "dut_tb")') == "nil"
        windows = bridge.skill("hiGetWindowList('all)")
        pre = mcp_call(server, "preview_template_symbol", args)
        assert pre["ok"], pre
        made = mcp_call(
            server,
            "create_template_symbol",
            dict(
                preview_ref=pre["preview_ref"],
                preview_digest=pre["preview_digest"],
                request_id="symbol",
            ),
        )
        assert made["ok"] and made["saved"] and made["readback_verified"], made
        assert bridge.skill("hiGetWindowList('all)") == windows
        assert bridge.skill(snapshot) == original
        occupied = mcp_call(server, "preview_template_symbol", dict(args, request_id="occupied"))
        assert not occupied["ok"] and "already exists" in occupied["message"]
        foreign = mcp_call(
            server, "preview_template_symbol", dict(args, cell="other", request_id="other")
        )
        assert not foreign["ok"] and "source design unit" in foreign["message"]
        binding = mcp_call(
            server,
            "get_symbol_binding",
            dict(symbol_ref=made["symbol_ref"], master_id="dut", instance_id="dut"),
        )
        assert binding["ok"], binding
        request = tb_request(binding)
        # The native entry independently rejects a schematic master even if Python is bypassed.
        invalid = payload(request)
        invalid[1][0][1][2] = "schematic"
        denied = json.loads(
            bridge.skill(
                'aiCreatePrepare("task:tb" "schematic" "digest" ' + skill_literal(invalid) + ")"
            )
        )
        assert not denied["ok"] and "require symbol view" in denied["message"]
        assert bridge.skill('ddGetObj("SicoTest" "dut_tb")') == "nil"
        prepared = mcp_call(server, "prepare_circuit_creation", request)
        assert prepared["ok"], prepared
        created = mcp_call(
            server,
            "create_circuit_from_plan",
            dict(
                prepare_ref=prepared["prepare_ref"],
                geometry_plan_digest=request["geometry_plan_digest"],
                request_id="tb",
            ),
        )
        assert created["ok"] and created["saved"] and created["readback_verified"], created
        assert created["check"] == [0, 0]
        instances = json.loads(
            bridge.skill(
                'let((cv result) cv=dbOpenCellViewByType("SicoTest" "dut_tb" "schematic" "" "r") '
                "result=aiCircuitJson(foreach(mapcar i cv~>instances "
                "list(i~>libName i~>cellName i~>viewName))) "
                "dbClose(cv) result)"
            )
        )
        assert instances.count(["SicoTest", "dut", "symbol"]) == 1
        assert all(
            lib == "basic" or [lib, cell, view] == ["SicoTest", "dut", "symbol"]
            for lib, cell, view in instances
        )
        assert bridge.skill(snapshot) == original
        assert bridge.skill("hiGetWindowList('all)") == windows
