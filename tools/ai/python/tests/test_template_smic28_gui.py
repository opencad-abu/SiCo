"""Opt-in actual archived topology -> SMIC OA -> Symbol/TB -> nominal Spectre."""

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from circuit_create_support import AI_ROOT
from circuit_smic28_fixture import testbench as tb_input
from template_smic28_fixture import (
    DUT_CELL,
    INV_REF,
    geometry_input,
    migration_input,
    simulation_recipe,
    verify_generated_views,
    write_json,
)
from template_smic28_support import migration_session
from test_circuit_rc_gui import mcp_call
from test_simulation_recipe_failures_gui import terminal

from cadai.template_catalog import TemplateCatalog


def test_archived_inv_to_smic28(tmp_path, monkeypatch):
    verify_archived_inv_to_smic28(tmp_path, monkeypatch)


def verify_archived_inv_to_smic28(tmp_path, monkeypatch, *, outline=False):
    if os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1" or not os.environ.get("CAD_AI_SMIC28_PDK"):
        pytest.skip("requires licensed Virtuoso and explicit SMIC28 acceptance PDK")
    source_workspace = AI_ROOT.parent
    record, origin = TemplateCatalog(workspace=source_workspace).locate(INV_REF)
    catalog = Path(origin["catalog_origin"]["catalog"])
    before = hashlib.sha256(catalog.read_bytes()).hexdigest()
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", origin["catalog_origin"]["root"])
    pdk = Path(os.environ["CAD_AI_SMIC28_PDK"]).resolve()
    with migration_session(tmp_path) as (bridge, server, _):
        with (tmp_path / "cds.lib").open("a") as f:
            f.write("DEFINE smic28hkmg " + str(pdk / "smic28hkmg") + "\n")
        project = tmp_path / "project"
        project.mkdir()
        bridge.skill(
            'progn(ddUpdateLibList() ddCreateLib("smicFlowTest" '
            + json.dumps(str(project / "library"))
            + "))"
        )
        for module in [
            "AI_templates.il",
            "AI_circuit_symbol.il",
            "AI_template_symbol_geometry.il",
            "AI_template_symbol_draw.il",
            "AI_template_symbol.il",
            "AI_template_symbol_binding.il",
            "AI_maestro_results.il",
            "AI_simulation_check.il",
            "AI_simulation_config.il",
            "AI_simulation_dimensions.il",
            "AI_simulation_coordinates.il",
            "AI_simulation_setup.il",
            "AI_simulation_run.il",
        ]:
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / module)) + ")")
        bridge.skill(
            "load(" + json.dumps(str(AI_ROOT / "python/tests/smic28_project_extension.il")) + ")"
        )
        args, geometry_rows, captured = migration_input(bridge, tmp_path)
        assert captured == record
        bridge.skill('aiSmicTestInstall(ddGetObjReadPath(ddGetObj("smic28hkmg")))')
        bridge.skill(
            'envSetVal("asimenv.startup" "projectDir" \'string '
            + json.dumps(str(project / "simulation"))
            + ")"
        )
        task = mcp_call(
            server,
            "begin_circuit_task",
            dict(task_id="migration", presentation="foreground", choice_confirmed=True),
        )

        def call(name, data, evidence):
            result = mcp_call(server, name, data)
            write_json(project / (evidence + ".json"), dict(request=data, result=result))
            assert result.get("ok", True), result
            return result

        def create(data, request):
            prep = call(
                "prepare_circuit_creation",
                dict(**data, task_ref=task["task_ref"], request_id=request),
                request + "-prepare",
            )
            result = call(
                "create_circuit_from_plan",
                dict(
                    prepare_ref=prep["prepare_ref"],
                    geometry_plan_digest=data["geometry_plan_digest"],
                    request_id=request,
                ),
                request + "-created",
            )
            assert result["readback_verified"] and result["check"] == [0, 0], result
            assert (
                mcp_call(
                    server,
                    "create_circuit_from_plan",
                    dict(
                        prepare_ref=prep["prepare_ref"],
                        geometry_plan_digest=data["geometry_plan_digest"],
                        request_id=request,
                    ),
                )
                == result
            )
            return result

        query = call(
            "query_circuit_templates",
            dict(library="gsclib045", cell="INVX1", rule_version="20260910.4"),
            "query",
        )
        assert INV_REF in {r["template_ref"] for r in query["templates"]}
        call("get_circuit_template", dict(template_ref=INV_REF, section="topology"), "source")
        prepared = call("prepare_template_circuit", args, "adapted")
        data = geometry_input(prepared, geometry_rows)
        geo = call("preview_circuit_geometry", data, "geometry")
        assert geo["plan"]["geometry_checks_passed"], geo
        data["geometry_plan_digest"] = geo["geometry_plan_digest"]
        # A changed endpoint with the retained mapping is rejected before any OA creation.
        bad = copy.deepcopy(data)
        bad["spec"]["instances"][0]["connections"]["G"] = "OUT"
        denied = server.handle(
            dict(
                jsonrpc="2.0",
                id=1,
                method="tools/call",
                params=dict(
                    name="prepare_circuit_creation",
                    arguments=dict(**bad, task_ref=task["task_ref"], request_id="bad-endpoint"),
                ),
            )
        )
        assert "endpoints differ" in denied["error"]["message"], denied
        assert bridge.skill('ddGetObj("smicFlowTest" "archived_inv" "schematic")') == "nil"
        write_json(project / "endpoint-rejected.json", denied)
        dut = create(data, "dut")
        # The original globals must fail live validation against this local interface.
        wrong = mcp_call(
            server,
            "preview_template_symbol",
            dict(
                library="smicFlowTest",
                cell=DUT_CELL,
                template_ref=INV_REF,
                port_map=args["port_map"],
                task_ref=task["task_ref"],
                request_id="wrong-global",
            ),
        )
        assert not wrong["ok"], wrong
        symbol_pre = call(
            "preview_template_symbol",
            dict(
                library="smicFlowTest",
                cell=DUT_CELL,
                template_ref=INV_REF,
                port_map=args["port_map"],
                net_scope_map=args["net_scope_map"],
                task_ref=task["task_ref"],
                request_id="symbol",
                **(
                    dict(
                        style="template_outline",
                        outline_scale=4,
                        port_roles={"VDD": "power", "VSS": "ground"},
                    )
                    if outline
                    else {}
                ),
            ),
            "symbol-preview",
        )
        symbol = call(
            "draw_symbol" if outline else "create_template_symbol",
            dict(
                preview_ref=symbol_pre["preview_ref"],
                preview_digest=symbol_pre["preview_digest"],
                request_id="symbol",
            ),
            "symbol",
        )
        assert symbol["readback_verified"], symbol
        binding = (
            call(
                "get_symbol_binding",
                dict(symbol_ref=symbol["symbol_ref"], master_id="dut", instance_id="dut"),
                "symbol-binding",
            )
            if outline
            else None
        )
        tb = create(
            tb_input(bridge, project, "archived_inv_tb", dut_cell=DUT_CELL, symbol_binding=binding),
            "tb",
        )
        proof = json.loads(
            bridge.skill(
                'let((v r) v=dbOpenCellViewByType("smicFlowTest" "archived_inv" '
                '"schematic" "" "r") r=aiCircuitJson(list(foreach(mapcar n v~>nets '
                "list(n~>name aiCircuitBool(aiTplGlobal(n)))) foreach(mapcar i v~>instances "
                "list(i~>name i~>libName i~>cellName i~>xy i~>orient)))) dbClose(v) r)"
            )
        )
        assert all(row[1] is False for row in proof[0]), proof
        assert {r[2] for r in proof[1] if r[1] == "smic28hkmg"} == {"n09_ckt", "p09_ckt"}
        grounds = json.loads(
            bridge.skill(
                'let((v r) v=dbOpenCellViewByType("smicFlowTest" "archived_inv_tb" '
                '"schematic" "" "r") r=aiCircuitJson(list(length(setof(i v~>instances '
                'i~>libName=="basic" i~>cellName=="gnd")) '
                'length(setof(s v~>shapes s~>objType=="label" s~>theLabel=="gnd!")))) dbClose(v) r)'
            )
        )
        assert grounds == [4, 0]
        recipe = simulation_recipe(pdk)
        preview = call("preview_simulation_recipe", dict(recipe=recipe), "simulation-preview")
        setup = call(
            "create_simulation_setup",
            dict(
                recipe=recipe,
                recipe_digest=preview["recipe_digest"],
                task_ref=task["task_ref"],
                request_id="sim",
            ),
            "setup",
        )
        run = call(
            "run_simulation_setup", dict(setup_ref=setup["setup_ref"], request_id="sim"), "run"
        )
        status = terminal(server, run)
        write_json(project / "status.json", status)
        assert status["state"] == "completed", status
        results = call(
            "read_maestro_results",
            dict(context_ref=setup["context_ref"], history=run["history"]),
            "results",
        )
        report = call(
            "evaluate_measurement_specs",
            dict(
                result_ref=results["result_ref"],
                contract=dict(
                    source="SMIC28 INV smoke-test bounds; cross-PDK equivalence unqualified",
                    expected_test_points=preview["expected_test_points"],
                    specifications=[
                        dict(test="switching", output=n, unit="V", lower=lo, upper=hi)
                        for n, lo, hi in [
                            ("high", 0.89, 0.91),
                            ("low", -0.01, 0.01),
                            ("input_high", 0.899, 0.901),
                            ("input_low", -0.001, 0.001),
                        ]
                    ],
                ),
            ),
            "report",
        )
        assert report["qualification"] == "pass", report
        verify_generated_views(bridge, project, dut, symbol, tb, setup, call)
        if binding:
            assert (
                call(
                    "get_symbol_binding",
                    dict(symbol_ref=symbol["symbol_ref"], master_id="dut", instance_id="dut"),
                    "symbol-binding-after",
                )
                == binding
            )
        assert hashlib.sha256(catalog.read_bytes()).hexdigest() == before
        write_json(
            project / "acceptance.json",
            dict(
                template_ref=INV_REF,
                capture_sha256=record["capture_sha256"],
                catalog_sha256=before,
                catalog_unchanged=True,
                dut=dut,
                symbol=symbol,
                tb=tb,
                oa_readback=proof,
                ground_proof=grounds,
                status=status,
                report=report,
            ),
        )
