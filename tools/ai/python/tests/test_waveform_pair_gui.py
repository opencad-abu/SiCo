"""Foreground Maestro -> exact pair artifacts -> measurements against native/analytic values."""

import json
import math
import os

import pytest
from template_smic28_support import migration_session
from test_circuit_rc_gui import AI_ROOT, mcp_call
from test_simulation_recipe_failures_gui import terminal
from test_simulation_recipe_gui import load_backend
from waveform_pair_fixture import measurement_recipes, setup_recipe


def test_generic_paired_measurements(tmp_path):
    if os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1":
        pytest.skip("licensed Virtuoso/Spectre acceptance")
    with migration_session(tmp_path) as (bridge, server, proc):
        if bridge.skill('ddGetObj("genericWork")') == "nil":
            bridge.skill('ddCreateLib("genericWork" ' + json.dumps(str(tmp_path / "library")) + ")")
        load_backend(bridge, tmp_path)
        bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill/AI_maestro_waveform.il")) + ")")
        bridge.skill(
            "load(" + json.dumps(str(AI_ROOT / "python/tests/waveform_pair_fixture.il")) + ")"
        )
        assert bridge.skill("pairFixtureCreate()") == "t"

        def call(name, **args):
            return mcp_call(server, name, args)

        task = call(
            "begin_circuit_task", task_id="pair", presentation="foreground", choice_confirmed=True
        )
        recipe = setup_recipe()
        preview = call("preview_simulation_recipe", recipe=recipe)
        setup = call(
            "create_simulation_setup",
            recipe=recipe,
            recipe_digest=preview["recipe_digest"],
            task_ref=task["task_ref"],
            request_id="pair",
        )
        assert setup["ok"] and setup["saved"] and setup["readback_verified"], setup
        run = call("run_simulation_setup", setup_ref=setup["setup_ref"], request_id="pair")
        status = terminal(server, run)
        assert status["state"] == "completed" and status["progress"] == [4, 4], status
        scalar = call(
            "read_maestro_results", context_ref=setup["context_ref"], history=run["history"]
        )
        coordinates = call(
            "query_maestro_results", result_ref=scalar["result_ref"], entity="coordinates"
        )["items"]
        assert len(coordinates) == 4, coordinates
        reports = []
        refs = []
        for coord in coordinates:
            args = dict(
                context_ref=setup["context_ref"],
                history=run["history"],
                test="transient_response",
                corner=coord["corner"],
                point=coord["point"],
                analysis="tran",
            )
            first = call("read_maestro_waveform", **args, signal="/IN")
            second = call("read_maestro_waveform", **args, signal="/OUT")
            assert first["ok"] and second["ok"], (first, second)
            refargs = dict(
                input_ref=first["waveform_ref"],
                output_ref=second["waveform_ref"],
                recipes=measurement_recipes(),
            )
            report = call("measure_waveform_pair", **refargs)
            assert report["all_measured"], report
            assert report == call("measure_waveform_pair", **refargs)
            vals = {r["id"]: r["value"] for r in report["rows"]}
            params = {p["name"]: p["value"] for p in coord["parameters"]}
            tau = float(params["RVAL"]) * 1e-9
            assert vals["delay"] == pytest.approx(tau * math.log(2) * 1e9, abs=2)
            assert vals["gain_sample"] == pytest.approx(1 - math.exp(-1.001e-6 / tau), abs=0.002)
            width = 2e-6
            expected = math.sqrt(
                1
                - 2 * tau / width * (1 - math.exp(-width / tau))
                + tau / (2 * width) * (1 - math.exp(-2 * width / tau))
            )
            assert vals["gain_rms"] == pytest.approx(expected, abs=0.002)
            native = json.loads(
                bridge.skill(
                    'progn(pairIn=getData("/IN" ?result \'tran ?resultsDir '
                    + json.dumps(first["source"]["results_directory"])
                    + ") "
                    'pairOut=getData("/OUT" ?result \'tran ?resultsDir '
                    + json.dumps(second["source"]["results_directory"])
                    + ") "
                    'aiCircuitJson(list(delay(?wf1 pairIn ?value1 .5 ?edge1 "rising" ?nth1 1 '
                    '?td1 .5e-6 ?wf2 pairOut ?value2 .5 ?edge2 "rising" ?nth2 1 '
                    "?td2r0 .5e-6 ?stop 4e-6) "
                    "value(pairOut 2.001e-6)/value(pairIn 2.001e-6) "
                    "rms(clip(pairOut 1.001e-6 3.001e-6))/rms(clip(pairIn 1.001e-6 3.001e-6)))))"
                )
            )
            assert vals["delay"] * 1e-9 == pytest.approx(native[0], rel=0, abs=1e-18)
            assert vals["gain_sample"] == pytest.approx(native[1], rel=0, abs=1e-12)
            assert vals["gain_rms"] == pytest.approx(native[2], rel=0, abs=2e-5)
            reports.append(dict(coordinate=coord, report=report, native=native))
            refs.append(refargs)
        mismatch = call("measure_waveform_pair", **dict(refs[0], output_ref=refs[-1]["output_ref"]))
        assert not mismatch["ok"], mismatch
        missing = call("read_maestro_waveform", **args, signal="/absent_signal")
        absent = call("measure_waveform_pair", **dict(refs[-1], output_ref=missing["waveform_ref"]))
        assert not absent["all_measured"] and all(r["status"] == "missing" for r in absent["rows"])
        assert call("inspect_simulation_setup", setup_ref=setup["setup_ref"])["ok"]
        bridge.skill(
            'awvPlotWaveform(awvCreatePlotWindow() list(pairIn pairOut) ?expr list("IN" "OUT"))'
        )
        call("release_design_context", context_ref=setup["context_ref"])
        assert call("measure_waveform_pair", **refs[-1]) == reports[-1]["report"]
        (tmp_path / "acceptance.json").write_text(
            json.dumps(
                dict(
                    setup=setup,
                    run=run,
                    status=status,
                    reports=reports,
                    mismatch=mismatch,
                    missing=absent,
                    pid=proc.pid,
                    offline_after_release=True,
                ),
                indent=2,
            )
        )
