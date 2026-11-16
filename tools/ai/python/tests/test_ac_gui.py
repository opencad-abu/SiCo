"""Exact-history foreground AC transport versus native Calculator and analytic RC."""

import cmath
import json
import math
import os

import pytest
from ac_fixture import ac_setup, point_recipes
from template_smic28_support import migration_session
from test_circuit_rc_gui import AI_ROOT, mcp_call
from test_simulation_recipe_failures_gui import terminal
from test_simulation_recipe_gui import load_backend
from test_waveform_measure import quantity


def test_foreground_complex_ac(tmp_path):
    if os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1":
        pytest.skip("licensed Virtuoso/Spectre acceptance")
    with migration_session(tmp_path) as (bridge, server, proc):
        if bridge.skill('ddGetObj("genericWork")') == "nil":
            bridge.skill('ddCreateLib("genericWork" ' + json.dumps(str(tmp_path / "library")) + ")")
        load_backend(bridge, tmp_path)
        for name in [
            "skill/AI_maestro_waveform.il",
            "skill/AI_maestro_ac.il",
            "python/tests/waveform_pair_fixture.il",
        ]:
            bridge.skill("load(" + json.dumps(str(AI_ROOT / name)) + ")")
        assert bridge.skill("pairFixtureCreate()") == "t"
        # Fixture source: 2 V / 30 degrees exposes absolute/relative phase confusion.
        bridge.skill(
            "let((acfCv acfV) "
            'acfCv=dbOpenCellViewByType("genericWork" "paired_rc" "schematic" "" "a") '
            'acfV=dbFindAnyInstByName(acfCv "V1") '
            'unless(and(cdfFindParamByName(cdfGetInstCDF(acfV) "acm") '
            'cdfFindParamByName(cdfGetInstCDF(acfV) "acp")) error("fixture source CDF missing")) '
            'dbReplaceProp(acfV "acm" "string" "2") dbReplaceProp(acfV "acp" "string" "30") '
            'unless(equal(schCheck(acfCv) list(0 0)) error("fixture schematic check")) '
            "dbSave(acfCv) dbClose(acfCv))"
        )

        def call(name, **args):
            return mcp_call(server, name, args)

        task = call(
            "begin_circuit_task", task_id="ac", presentation="foreground", choice_confirmed=True
        )
        recipe = ac_setup()
        preview = call("preview_simulation_recipe", recipe=recipe)
        setup = call(
            "create_simulation_setup",
            recipe=recipe,
            recipe_digest=preview["recipe_digest"],
            task_ref=task["task_ref"],
            request_id="ac",
        )
        assert setup["ok"] and setup["readback_verified"], setup
        before = call("inspect_simulation_setup", setup_ref=setup["setup_ref"])
        run = call("run_simulation_setup", setup_ref=setup["setup_ref"], request_id="ac")
        status = terminal(server, run)
        assert status["state"] == "completed" and status["progress"] == [4, 4], status
        anchor = call(
            "read_maestro_results", context_ref=setup["context_ref"], history=run["history"]
        )
        captures, reports = [], []
        for point in preview["expected_test_points"]:
            args = dict(
                context_ref=setup["context_ref"], history=run["history"], analysis="ac", **point
            )
            first = call("read_maestro_ac_waveform", **args, signal="/IN")
            second = call("read_maestro_ac_waveform", **args, signal="/OUT")
            assert first["ok"] and second["ok"], (first, second)
            assert second["sample_count"] == 401 and second["native_y_type"] == "doublecomplex", (
                second
            )
            assert (second["x_unit"], second["y_unit"]) == ("Hz", "V")
            assert (
                first["source"]["result_ref"]
                == second["source"]["result_ref"]
                == anchor["result_ref"]
            )
            assert (
                call("read_maestro_ac_waveform", **args, signal="/OUT")["waveform_ref"]
                == second["waveform_ref"]
            )
            params = {p["name"]: p["value"] for p in second["source"]["coordinate"]["parameters"]}
            tau = float(params["RVAL"]) * 1e-9
            bridge.skill(
                'progn(acNativeIn=getData("/IN" ?result \'ac ?resultsDir '
                + json.dumps(first["source"]["results_directory"])
                + ") "
                'acNativeOut=getData("/OUT" ?result \'ac ?resultsDir '
                + json.dumps(second["source"]["results_directory"])
                + ") t)"
            )
            for frequency in [1 / (2 * math.pi * 1e-6), 1e6]:
                single = call(
                    "measure_ac_waveform",
                    waveform_ref=second["waveform_ref"],
                    recipes=point_recipes(frequency),
                )
                paired_args = dict(
                    input_ref=first["waveform_ref"],
                    output_ref=second["waveform_ref"],
                    denominator_floor=quantity(1, "mV"),
                    recipes=point_recipes(frequency, True),
                )
                pair = call("measure_ac_transfer", **paired_args)
                assert single["all_measured"] and pair["all_measured"], (single, pair)
                native = json.loads(
                    bridge.skill(
                        f"progn(acNativeZ=value(acNativeOut {frequency!r}) "
                        f"acNativeH=acNativeZ/value(acNativeIn {frequency!r}) "
                        "aiCircuitJson(list(real(acNativeZ) imag(acNativeZ) "
                        "mag(acNativeZ) phaseDeg(acNativeZ) "
                        "real(acNativeH) imag(acNativeH) mag(acNativeH) "
                        "phaseDeg(acNativeH) phaseRad(acNativeH))))"
                    )
                )
                z = single["rows"][0]["components"]["phasor"]
                h = pair["rows"][0]["components"]["phasor"]
                assert [
                    z["real"],
                    z["imaginary"],
                    single["rows"][0]["value"] / 1000,
                    single["rows"][2]["value"],
                ] == pytest.approx(native[:4], abs=1e-10)
                assert [
                    h["real"],
                    h["imaginary"],
                    pair["rows"][0]["value"],
                    pair["rows"][2]["value"],
                    pair["rows"][3]["value"],
                ] == pytest.approx(native[4:], abs=1e-10)
                expected = 1 / complex(1, 2 * math.pi * frequency * tau)
                assert pair["rows"][0]["value"] == pytest.approx(abs(expected), rel=0.0003)
                assert pair["rows"][2]["value"] == pytest.approx(
                    math.degrees(cmath.phase(expected)), abs=0.006
                )
                assert pair["rows"][1]["value"] == pytest.approx(
                    20 * math.log10(abs(expected)), abs=0.003
                )
                assert single["rows"][2]["value"] - pair["rows"][2]["value"] == pytest.approx(
                    30, abs=1e-10
                )
                reports.append(
                    dict(
                        point=point, frequency_hz=frequency, single=single, pair=pair, native=native
                    )
                )
            captures.append(dict(args=args, first=first, second=second))

        saved = call(
            "query_maestro_results",
            result_ref=anchor["result_ref"],
            test="small_signal",
            corner="cool",
            point=1,
        )["items"]
        first_report = next(
            r
            for r in reports
            if r["point"]["corner"] == "cool"
            and r["point"]["point"] == 1
            and r["frequency_hz"] == 1e6
        )
        assert next(r["value"] for r in saved if r["output"] == "saved_mag") == pytest.approx(
            first_report["single"]["rows"][0]["value"] / 1000, abs=1e-12
        )
        assert next(r["value"] for r in saved if r["output"] == "saved_phase") == pytest.approx(
            first_report["single"]["rows"][2]["value"], abs=1e-10
        )
        # Preserve result selection and the old real-only complex marker behavior.
        bridge.skill(
            "progn(openResults("
            + json.dumps(second["source"]["results_directory"] + "/psf")
            + ") selectResult('dcOp) t)"
        )
        selection = bridge.skill('aiCircuitJson(list(openResults() getData("/OUT")))')
        old = captures[0]
        old_real = call("read_maestro_waveform", **old["args"], signal="/OUT")
        assert old_real["status"] == "complex" and old_real["sample_count"] == 0
        assert (
            call("read_maestro_ac_waveform", **old["args"], signal="/OUT")["waveform_ref"]
            == old["second"]["waveform_ref"]
        )
        assert bridge.skill('aiCircuitJson(list(openResults() getData("/OUT")))') == selection
        missing = call("read_maestro_ac_waveform", **args, signal="/absent")
        absent = call(
            "measure_ac_waveform", waveform_ref=missing["waveform_ref"], recipes=point_recipes(1e6)
        )
        assert all(r["status"] == "missing" for r in absent["rows"])
        mismatch = call(
            "measure_ac_transfer", **dict(paired_args, input_ref=old["first"]["waveform_ref"])
        )
        assert not mismatch["ok"], mismatch
        assert bridge.skill("length(aiWaveCaptures)") == "0"
        assert bridge.skill("length(aiResultsCaptures)") == "0"
        # Native type and principal-branch probes; real phasors explicitly serialize imaginary zero.
        probes = json.loads(
            bridge.skill(
                "aiCircuitJson(list(aiAcFreeze(drCreateWaveform(drCreateVec('double list(1 2)) "
                "drCreateVec('double list(3 4)))) "
                "aiAcFreeze(drCreateWaveform(drCreateVec('double list(1 2)) "
                "drCreateVec('doublecomplex list(complex(-1 0) complex(0 -1))))) "
                "phaseDeg(complex(-1 0)) phaseRad(complex(0 -1)) "
                'car(aiAcFreeze(famAddValue(famCreateFamily("sweep" \'double) 1.0 acNativeOut))))'
            )
        )
        assert probes[0][5] == "double" and json.loads(probes[0][2][0])[0] == [1, 3, 0]
        assert probes[1][5] == "doublecomplex" and json.loads(probes[1][2][0])[1] == [2, 0, -1]
        assert (
            probes[2] == 180 and probes[3] == pytest.approx(-math.pi / 2) and probes[4] == "family"
        )
        newer = call("run_simulation_setup", setup_ref=setup["setup_ref"], request_id="ac_again")
        assert (
            terminal(server, newer)["state"] == "completed" and newer["history"] != run["history"]
        )
        assert (
            call("read_maestro_ac_waveform", **old["args"], signal="/OUT")["waveform_ref"]
            == old["second"]["waveform_ref"]
        )
        assert call("inspect_simulation_setup", setup_ref=setup["setup_ref"]) == before
        bridge.skill(
            "awvPlotWaveform(awvCreatePlotWindow() list(mag(acNativeOut/acNativeIn) "
            "phaseDeg(acNativeOut/acNativeIn)) "
            '?expr list("Transfer magnitude" "Transfer phase (deg)"))'
        )
        call("release_simulation_setup", setup_ref=setup["setup_ref"])
        assert call("measure_ac_transfer", **paired_args) == reports[-1]["pair"]
        assert (
            call(
                "query_ac_waveform",
                waveform_ref=old["second"]["waveform_ref"],
                offset=256,
                limit=100,
            )["total"]
            == 401
        )
        bridge.skill("procedure(witMenuToolsRoot() " + json.dumps(str(AI_ROOT.parent)) + ")")
        for _ in range(2):
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill++/AI.ils")) + ")")
        assert bridge.skill("aiLoaderRevision()") == "20260915.ai.loader.v30"
        assert bridge.skill("aiAcRevision()") == "20260911.ai.ac.v1"
        (tmp_path / "acceptance.json").write_text(
            json.dumps(
                dict(
                    setup=setup,
                    run=run,
                    status=status,
                    anchor=anchor,
                    captures=captures,
                    reports=reports,
                    missing=absent,
                    mismatch=mismatch,
                    probes=probes,
                    newer=newer,
                    setup_unchanged=True,
                    selection_unchanged=True,
                    old_history_verified=True,
                    offline_after_release=True,
                    pid=proc.pid,
                ),
                indent=2,
            )
        )
