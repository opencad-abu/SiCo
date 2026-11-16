"""Real PDK -> generic DUT -> template Symbol -> grounded TB -> nominal Maestro."""

import json
import os
from pathlib import Path

import pytest
from circuit_create_support import AI_ROOT, session
from circuit_smic28_fixture import inverter
from circuit_smic28_fixture import testbench as tb_input
from test_circuit_rc_gui import mcp_call
from test_simulation_recipe_failures_gui import terminal

from cadai.template_build import make_template, publish
from cadai.template_capture import read_capture


def test_smic28_grounded_flow(tmp_path, monkeypatch):
    if os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1" or not os.environ.get("CAD_AI_SMIC28_PDK"):
        pytest.skip("requires licensed GUI and explicit CAD_AI_SMIC28_PDK fixture root")
    pdk = Path(os.environ["CAD_AI_SMIC28_PDK"]).resolve()
    with session(tmp_path) as (bridge, server, _):
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
            "AI_template_capture_geometry.il",
            "AI_template_native_content.il",
            "AI_circuit_symbol.il",
            "AI_template_symbol_geometry.il",
            "AI_template_symbol.il",
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
        # Ensure the PDK libInit/CDF has been loaded before registration.
        data = inverter(bridge, project)
        bridge.skill('aiSmicTestInstall(ddGetObjReadPath(ddGetObj("smic28hkmg")))')
        bridge.skill(
            'envSetVal("asimenv.startup" "projectDir" \'string '
            + json.dumps(str(project / "simulation"))
            + ")"
        )
        task = mcp_call(
            server,
            "begin_circuit_task",
            dict(task_id="flow", presentation="foreground", choice_confirmed=True),
        )

        def create(args, request):
            pre = mcp_call(
                server,
                "prepare_circuit_creation",
                dict(**args, task_ref=task["task_ref"], request_id=request),
            )
            assert pre["ok"], pre
            result = mcp_call(
                server,
                "create_circuit_from_plan",
                dict(
                    prepare_ref=pre["prepare_ref"],
                    geometry_plan_digest=args["geometry_plan_digest"],
                    request_id=request,
                ),
            )
            assert result["ok"] and result["readback_verified"] and result["check"] == [0, 0], (
                result
            )
            return result

        dut = create(data, "dut")
        # Copy through the public database API to an independent test reference library.
        bridge.skill(
            'let((v c) ddCreateLib("smicReference" ' + json.dumps(str(project / "reference")) + ") "
            'v=dbOpenCellViewByType("smicFlowTest" "smic_inv" "schematic" "" "r") '
            'c=dbCopyCellView(v "smicReference" "smic_inv" "schematic") '
            'unless(c error("test reference copy failed")) dbSave(c) dbClose(c) dbClose(v) t)'
        )
        capture = project / "reference.jsonl"
        bridge.skill(
            'aiTplCapture("smicReference" "smic_inv" "schematic" "symbol" "layout" '
            + json.dumps(str(capture))
            + ' list("schematic"))'
        )
        template = make_template(read_capture(capture))
        catalog = project / "templates"
        catalog.mkdir()
        publish([template], catalog / "catalog.sqlite3")
        monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(catalog))
        pre = mcp_call(
            server,
            "preview_template_symbol",
            dict(
                library="smicFlowTest",
                cell="smic_inv",
                template_ref=template["template_ref"],
                task_ref=task["task_ref"],
                request_id="symbol",
            ),
        )
        assert pre["ok"], pre
        symbol = mcp_call(
            server,
            "create_template_symbol",
            dict(
                preview_ref=pre["preview_ref"],
                preview_digest=pre["preview_digest"],
                request_id="symbol",
            ),
        )
        assert symbol["ok"] and symbol["readback_verified"], symbol
        tb = create(tb_input(bridge, project), "tb")
        ground_proof = json.loads(
            bridge.skill(
                'let((v r) v=dbOpenCellViewByType("smicFlowTest" "smic_inv_tb" "schematic" "" "r") '
                "r=aiCircuitJson(list(length(setof(i v~>instances "
                'i~>libName=="basic" i~>cellName=="gnd")) '
                'length(setof(s v~>shapes s~>objType=="label" s~>theLabel=="gnd!")))) dbClose(v) r)'
            )
        )
        assert ground_proof == [4, 0]
        models = pdk / "models/spectre"
        recipe = dict(
            schema="cad.simulation.recipe.v1",
            target=dict(library="smicFlowTest", cell="smic_inv_sim", view="maestro"),
            variables={},
            temperature_c=27,
            tests=[
                dict(
                    name="switching",
                    design=dict(library="smicFlowTest", cell="smic_inv_tb", view="schematic"),
                    simulator="spectre",
                    variables={},
                    models=[dict(path=str(models / "l0028hk_io25_v1p0_spe.lib"), section="TT")],
                    analyses=[
                        dict(
                            name="tran", options=dict(stop="5n", maxstep="5p", errpreset="moderate")
                        )
                    ],
                    outputs=[
                        dict(name=n, kind="expression", value=e, plot=True, save=True)
                        for n, e in [
                            ("high", 'value(VT("/OUT") 0.5e-9)'),
                            ("low", 'value(VT("/OUT") 1.5e-9)'),
                        ]
                    ],
                    switch_views=["spectre", "schematic"],
                    stop_views=["spectre"],
                )
            ],
        )
        preview = mcp_call(server, "preview_simulation_recipe", dict(recipe=recipe))
        setup = mcp_call(
            server,
            "create_simulation_setup",
            dict(
                recipe=recipe,
                recipe_digest=preview["recipe_digest"],
                task_ref=task["task_ref"],
                request_id="sim",
            ),
        )
        assert setup["ok"], setup
        run = mcp_call(
            server, "run_simulation_setup", dict(setup_ref=setup["setup_ref"], request_id="sim")
        )
        status = terminal(server, run)
        assert status["state"] == "completed", status
        results = mcp_call(
            server,
            "read_maestro_results",
            dict(context_ref=setup["context_ref"], history=run["history"]),
        )
        assert results["ok"], results
        report = mcp_call(
            server,
            "evaluate_measurement_specs",
            dict(
                result_ref=results["result_ref"],
                contract=dict(
                    source="Explicit SMIC28 inverter smoke-test voltage bounds",
                    expected_test_points=preview["expected_test_points"],
                    specifications=[
                        dict(test="switching", output=n, unit="V", lower=lo, upper=hi)
                        for n, lo, hi in [("high", 0.89, 0.91), ("low", -0.01, 0.01)]
                    ],
                ),
            ),
        )
        assert report["qualification"] == "pass", report
        netlists = list((project / "simulation").rglob("netlist/input.scs"))
        netlist = next(
            p.read_text() for p in netlists if "I_smic_inv_0 (IN OUT VDD 0)" in p.read_text()
        )
        for text in [
            "MN (OUT IN VSS VSS) n09_ckt w=1u l=60n nf=2",
            "MP (OUT IN VDD VDD) p09_ckt w=2u l=60n nf=2",
            "VSUP (VDD 0) vsource dc=0.9",
            "CL (OUT 0) capacitor c=5f",
        ]:
            assert text in netlist
        assert mcp_call(server, "inspect_simulation_setup", dict(setup_ref=setup["setup_ref"]))[
            "ok"
        ]
        (project / "flow.json").write_text(
            json.dumps(
                dict(
                    dut=dut,
                    symbol=symbol,
                    tb=tb,
                    ground_proof=ground_proof,
                    setup=setup,
                    status=status,
                    report=report,
                ),
                indent=2,
            )
        )
