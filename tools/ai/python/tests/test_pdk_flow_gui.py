"""Formal discovery -> template DUT/Symbol -> pin-grounded TB -> Config/Maestro."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from circuit_create_support import AI_ROOT
from pdk_flow_fixture import (
    DUT_CELL,
    INV_REF,
    dut_geometry,
    dut_input,
    recipe,
    select_devices,
    tb_input,
)
from pdk_flow_verify import verify_flow
from template_smic28_fixture import verify_view_readback, write_json
from template_smic28_support import migration_session
from test_circuit_rc_gui import mcp_call
from test_simulation_recipe_failures_gui import terminal
from test_simulation_recipe_gui import load_backend

from cadai.template_catalog import TemplateCatalog


def test_formal_pdk_template_to_config_maestro(tmp_path, monkeypatch):
    pdk_root = os.environ.get("SICO_AI_SMIC28_PDK")
    if os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1" or not pdk_root:
        pytest.skip("requires explicit SMIC28 PDK and licensed foreground acceptance")
    pdk = Path(pdk_root).resolve()
    record, origin = TemplateCatalog(workspace=AI_ROOT.parent).locate(INV_REF)
    catalog = Path(origin["catalog_origin"]["catalog"])
    before = hashlib.sha256(catalog.read_bytes()).hexdigest()
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", origin["catalog_origin"]["root"])
    with migration_session(tmp_path) as (bridge, server, proc):
        with (tmp_path / "cds.lib").open("a") as stream:
            stream.write("DEFINE smic28hkmg " + str(pdk / "smic28hkmg") + "\n")
        project = tmp_path / "project"
        project.mkdir()
        bridge.skill(
            'progn(ddUpdateLibList() ddCreateLib("smicFlowTest" '
            + json.dumps(str(project / "library"))
            + "))"
        )
        load_backend(bridge, project)
        for module in (
            "AI_pdk_core.il",
            "AI_pdk_init.il",
            "AI_pdk_cdf.il",
            "AI_pdk_db.il",
            "AI_pdk_capture.il",
            "AI_templates.il",
            "AI_circuit_symbol.il",
            "AI_template_symbol_geometry.il",
            "AI_template_symbol_draw.il",
            "AI_template_symbol.il",
            "AI_template_symbol_binding.il",
            "AI_circuit_config.il",
        ):
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / module)) + ")")
        bridge.skill(
            "load(" + json.dumps(str(AI_ROOT / "python/tests/smic28_project_extension.il")) + ")"
        )

        def call(name, args, label):
            result = mcp_call(server, name, args)
            write_json(project / (label + ".json"), dict(request=args, result=result))
            assert result.get("ok", True), result
            return result

        task = call(
            "begin_circuit_task",
            dict(task_id="formal_flow", presentation="foreground", choice_confirmed=True),
            "task",
        )
        records, pages = select_devices(server)
        write_json(project / "discovery.json", dict(records=records, pages=pages))
        bridge.skill('aiSmicTestInstall(ddGetObjReadPath(ddGetObj("smic28hkmg")))')

        def create(data, label):
            if "preview_digest" not in data:
                preview = call(
                    "preview_circuit_spec",
                    {k: data[k] for k in ("spec", "bindings")},
                    label + "-spec",
                )
                data["preview_digest"] = preview["preview_digest"]
            geo = call("preview_circuit_geometry", data, label + "-geometry")
            assert geo["plan"]["geometry_checks_passed"], geo
            pre = call(
                "prepare_circuit_creation",
                dict(
                    data,
                    geometry_plan_digest=geo["geometry_plan_digest"],
                    task_ref=task["task_ref"],
                    request_id=label,
                ),
                label + "-prepare",
            )
            execute = dict(
                prepare_ref=pre["prepare_ref"],
                geometry_plan_digest=geo["geometry_plan_digest"],
                request_id=label,
            )
            result = call("create_circuit_from_plan", execute, label + "-created")
            assert result["check"] == [0, 0] and result["readback_verified"]
            assert mcp_call(server, "create_circuit_from_plan", execute) == result
            return result

        mapping, rows = dut_input(record, records)
        prepared = call("prepare_template_circuit", mapping, "template-adapted")
        dut = create(dut_geometry(prepared, rows), "dut")
        sympre = call(
            "preview_template_symbol",
            dict(
                library="smicFlowTest",
                cell=DUT_CELL,
                template_ref=INV_REF,
                port_map=mapping["port_map"],
                net_scope_map=mapping["net_scope_map"],
                style="template_outline",
                outline_scale=4,
                port_roles=dict(VDD="power", VSS="ground"),
                task_ref=task["task_ref"],
                request_id="symbol",
            ),
            "symbol-preview",
        )
        symbol = call(
            "draw_symbol",
            dict(
                preview_ref=sympre["preview_ref"],
                preview_digest=sympre["preview_digest"],
                request_id="symbol",
            ),
            "symbol",
        )
        binding_args = dict(symbol_ref=symbol["symbol_ref"], master_id="dut", instance_id="dut")
        binding = call("get_symbol_binding", binding_args, "symbol-binding")
        tb = create(tb_input(records, binding), "tb")
        config = call(
            "create_circuit_config",
            dict(
                library="smicFlowTest",
                cell="archived_inv_tb",
                view="config",
                top_library="smicFlowTest",
                top_cell="archived_inv_tb",
                top_view="schematic",
                bindings=[
                    dict(
                        instance="I_archived_inv_0",
                        library="smicFlowTest",
                        cell=DUT_CELL,
                        view="schematic",
                    )
                ],
                switch_views=["spectre", "schematic"],
                stop_views=["spectre"],
                task_ref=task["task_ref"],
                request_id="config",
            ),
            "config",
        )
        call("inspect_circuit_config", dict(config_ref=config["config_ref"]), "config-inspect")
        sim = recipe(pdk, config["config_ref"])
        preview = call("preview_simulation_recipe", dict(recipe=sim), "simulation-preview")
        setup = call(
            "create_simulation_setup",
            dict(
                recipe=sim,
                recipe_digest=preview["recipe_digest"],
                task_ref=task["task_ref"],
                request_id="sim",
            ),
            "setup",
        )
        call("inspect_simulation_setup", dict(setup_ref=setup["setup_ref"]), "setup-before-run")
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
                    source="Explicit nominal SMIC28 smoke-test bounds, not cross-PDK equivalence",
                    expected_test_points=preview["expected_test_points"],
                    specifications=[
                        dict(test=t, output=n, unit="V", lower=lo, upper=hi)
                        for t in ("direct", "configured")
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
        proof = verify_flow(bridge, call, project, results)
        verify_view_readback(bridge, project, dut, symbol, tb, setup, call)
        assert call("get_symbol_binding", binding_args, "symbol-binding-after") == binding
        assert hashlib.sha256(catalog.read_bytes()).hexdigest() == before
        write_json(
            project / "acceptance.json",
            dict(
                pid=proc.pid,
                template_ref=INV_REF,
                catalog_unchanged=True,
                dut=dut,
                symbol=symbol,
                tb=tb,
                config=config,
                setup=setup,
                status=status,
                report=report,
                proof=proof,
                formal_pdk_masters=list(records),
            ),
        )
