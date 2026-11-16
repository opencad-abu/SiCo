"""Project SMIC smoke matrix on isolated copies of the accepted archived-template circuit."""

import copy
import json
import os
import re
from pathlib import Path

import pytest
from circuit_create_support import AI_ROOT
from template_smic28_fixture import simulation_recipe, write_json
from template_smic28_support import migration_session
from test_circuit_rc_gui import mcp_call
from test_simulation_recipe_failures_gui import terminal


@pytest.mark.skipif(os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed SMIC matrix")
def test_smic_corner_temperature_and_load_supply_sweeps(tmp_path):
    if not os.environ.get("CAD_AI_SMIC28_PDK") or not os.environ.get(
        "CAD_AI_SMIC_TEMPLATE_LIBRARY"
    ):
        pytest.skip("explicit PDK and phase15 reference work library required")
    pdk = Path(os.environ["CAD_AI_SMIC28_PDK"]).resolve()
    source = Path(os.environ["CAD_AI_SMIC_TEMPLATE_LIBRARY"]).resolve()
    with migration_session(tmp_path) as (b, s, proc):
        with (tmp_path / "cds.lib").open("a") as f:
            f.write(
                "DEFINE smic28hkmg "
                + str(pdk / "smic28hkmg")
                + "\nDEFINE migrationSource "
                + str(source)
                + "\n"
            )
        b.skill(
            'progn(ddUpdateLibList() ddCreateLib("smicFlowTest" '
            + json.dumps(str(tmp_path / "library"))
            + "))"
        )
        for module in [
            "AI_maestro_results.il",
            "AI_simulation_check.il",
            "AI_simulation_config.il",
            "AI_simulation_dimensions.il",
            "AI_simulation_coordinates.il",
            "AI_simulation_setup.il",
            "AI_simulation_run.il",
        ]:
            b.skill("load(" + json.dumps(str(AI_ROOT / "skill" / module)) + ")")
        b.skill(
            'envSetVal("asimenv.startup" "projectDir" \'string '
            + json.dumps(str(tmp_path / "simulation"))
            + ")"
        )
        task = mcp_call(
            s,
            "begin_circuit_task",
            dict(task_id="smic_matrix", presentation="foreground", choice_confirmed=True),
        )

        def source_snapshot():
            return b.skill(
                "let((cv rows) unwindProtect(progn(foreach(target "
                '\'(("archived_inv" "schematic") ("archived_inv" "symbol") '
                '("archived_inv_tb" "schematic")) '
                'cv=dbOpenCellViewByType("migrationSource" car(target) cadr(target) "" "r") '
                'unless(cv error("reference view unavailable")) '
                "rows=cons(list(target aiSimSourceSnapshot(cv)) rows) dbClose(cv) cv=nil) "
                "aiCircuitJson(reverse(rows))) when(cv dbClose(cv))))"
            )

        original_source = source_snapshot()
        (tmp_path / "source-before.json").write_text(original_source)
        # Public OA copy into a separate test work library; never copy internal OA files.
        for cell, view in [
            ("archived_inv", "schematic"),
            ("archived_inv", "symbol"),
            ("archived_inv_tb", "schematic"),
        ]:
            b.skill(
                'let((src dst) src=dbOpenCellViewByType("migrationSource" '
                + json.dumps(cell)
                + " "
                + json.dumps(view)
                + ' "" "r") '
                'dst=dbCopyCellView(src "smicFlowTest" '
                + json.dumps(cell)
                + " "
                + json.dumps(view)
                + ") "
                'unless(dst error("fixture copy failed")) dbSave(dst) dbClose(dst) dbClose(src) t)'
            )
        b.skill(
            'let((v) v=dbOpenCellViewByType("smicFlowTest" "archived_inv_tb" "schematic" "" "a") '
            'dbReplaceProp(dbFindAnyInstByName(v "VSUP") "vdc" "string" "VDDVAL") '
            'dbReplaceProp(dbFindAnyInstByName(v "VIN") "v2" "string" "VDDVAL") '
            'dbReplaceProp(dbFindAnyInstByName(v "CL") "c" "string" "CLOAD") '
            'unless(equal(schCheck(v) list(0 0)) error("fixture check failed")) '
            "dbSave(v) dbClose(v))"
        )
        # Open this new testbench for the user's requested foreground workflow.
        b.skill(
            'smicMatrixTbWindow=deOpenCellView("smicFlowTest" "archived_inv_tb" '
            '"schematic" "schematic" nil "r")'
        )
        recipe = simulation_recipe(pdk)
        recipe["target"]["cell"] = "archived_inv_pvt"
        recipe["variables"] = dict(CLOAD="5f", VDDVAL="0.9")
        recipe["tests"][0]["models"] = []
        dc = copy.deepcopy(recipe["tests"][0])
        dc["name"] = "bias"
        dc["analyses"] = [dict(name="dc", options=dict(saveOppoint=True))]
        dc["outputs"] = [
            dict(name="dc_high", kind="expression", value='VDC("/OUT")', plot=True, save=True)
        ]
        recipe["tests"].append(dc)
        recipe["corners"] = [
            dict(
                name=n,
                temperature_c=t,
                variables={},
                models=[
                    dict(path=str(pdk / "models/spectre/l0028hk_io25_v1p0_spe.lib"), section=n)
                ],
            )
            for n, t in [("tt", 27), ("ff", -40), ("ss", 125)]
        ]
        recipe["sweeps"] = [
            dict(variable="CLOAD", values=[5e-15, 1e-14]),
            dict(variable="VDDVAL", values=[0.8, 0.9]),
        ]

        def call(name, args, tag):
            result = mcp_call(s, name, args)
            write_json(tmp_path / (tag + ".json"), dict(request=args, result=result))
            assert result.get("ok", True), result
            return result

        pre = call("preview_simulation_recipe", dict(recipe=recipe), "preview")
        setup = call(
            "create_simulation_setup",
            dict(
                recipe=recipe,
                recipe_digest=pre["recipe_digest"],
                task_ref=task["task_ref"],
                request_id="smic_matrix",
            ),
            "setup",
        )
        assert setup["readback_verified"], setup
        run = call(
            "run_simulation_setup",
            dict(setup_ref=setup["setup_ref"], request_id="smic_matrix"),
            "run",
        )
        status = terminal(s, run)
        write_json(tmp_path / "status.json", status)
        assert status["state"] == "completed" and status["progress"] == [24, 24], status
        result = call(
            "read_maestro_results",
            dict(context_ref=setup["context_ref"], history=run["history"]),
            "results",
        )
        report = call(
            "evaluate_measurement_specs",
            dict(
                result_ref=result["result_ref"],
                contract=dict(
                    source="SMIC28 functional PVT smoke bounds; timing unqualified",
                    expected_test_points=pre["expected_test_points"],
                    specifications=[
                        dict(test=t, output=n, unit="V", lower=lo, upper=hi)
                        for t, n, lo, hi in [
                            ("switching", "high", 0.79, 0.91),
                            ("switching", "low", -0.01, 0.01),
                            ("switching", "input_high", 0.799, 0.901),
                            ("switching", "input_low", -0.001, 0.001),
                            ("bias", "dc_high", 0.79, 0.91),
                        ]
                    ],
                ),
            ),
            "report",
        )
        assert report["qualification"] == "pass", report
        coords = set()
        for p in (tmp_path / "simulation").rglob("netlist/input.scs"):
            if "psf" in p.parts or any(part.startswith(".tmpADEDir") for part in p.parts):
                continue
            text = p.read_text()
            design_text = (p.parent / "netlist").read_text()
            section = re.search(r'l0028hk_io25_v1p0_spe.lib" section=(tt|ff|ss)', text).group(1)
            temp = float(re.search(r"\btemp=([0-9.eE+-]+)", text).group(1))
            voltage = float(re.search(r"\bVDDVAL=([0-9.eE+-]+)", text).group(1))
            load = float(re.search(r"\bCLOAD=([0-9.eE+-]+)", text).group(1))
            assert temp == dict(tt=27, ff=-40, ss=125)[section]
            assert text.count("l0028hk_io25_v1p0_spe.lib") == 1
            assert "I_archived_inv_0 (IN OUT VDD 0)" in text + design_text
            coords.add((section, temp, voltage, load, p.parts[-3]))
        assert len(coords) == 24, coords
        call("inspect_simulation_setup", dict(setup_ref=setup["setup_ref"]), "inspect")
        final_source = source_snapshot()
        (tmp_path / "source-after.json").write_text(final_source)
        assert json.loads(original_source) == json.loads(final_source)
        write_json(
            tmp_path / "acceptance.json",
            dict(
                pid=proc.pid,
                status=status,
                report=report,
                netlist_coordinates=sorted(coords),
                source_unchanged=True,
            ),
        )
