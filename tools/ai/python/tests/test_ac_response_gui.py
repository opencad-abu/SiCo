"""Foreground RLC transfer: cutoff and declared loop-model margin against native/analytic."""

import json
import math
import os
from copy import deepcopy

import pytest
from ac_response_fixture import recipe, setup
from template_smic28_support import migration_session
from test_circuit_rc_gui import AI_ROOT, mcp_call
from test_simulation_recipe_failures_gui import terminal
from test_simulation_recipe_gui import load_backend
from test_waveform_measure import quantity


def test_foreground_response_measurements(tmp_path):
    if os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1":
        pytest.skip("licensed foreground Spectre acceptance")
    with migration_session(tmp_path) as (bridge, server, proc):
        if bridge.skill('ddGetObj("genericWork")') == "nil":
            bridge.skill('ddCreateLib("genericWork" ' + json.dumps(str(tmp_path / "library")) + ")")
        load_backend(bridge, tmp_path)
        for module in [
            "skill/AI_maestro_waveform.il",
            "skill/AI_maestro_ac.il",
            "python/tests/waveform_pair_fixture.il",
            "python/tests/ac_response_fixture.il",
        ]:
            bridge.skill("load(" + json.dumps(str(AI_ROOT / module)) + ")")
        assert bridge.skill("responseFixtureCreate()") == "t"

        def call(name, **args):
            return mcp_call(server, name, args)

        task = call(
            "begin_circuit_task",
            task_id="response",
            presentation="foreground",
            choice_confirmed=True,
        )
        data = setup()
        preview = call("preview_simulation_recipe", recipe=data)
        created = call(
            "create_simulation_setup",
            recipe=data,
            recipe_digest=preview["recipe_digest"],
            task_ref=task["task_ref"],
            request_id="response",
        )
        assert created["ok"] and created["readback_verified"], created
        before = call("inspect_simulation_setup", setup_ref=created["setup_ref"])
        run = call("run_simulation_setup", setup_ref=created["setup_ref"], request_id="response")
        status = terminal(server, run)
        assert status["state"] == "completed" and status["progress"] == [4, 4], status
        reports = []
        for point in preview["expected_test_points"]:
            request = dict(
                context_ref=created["context_ref"], history=run["history"], analysis="ac", **point
            )
            first = call("read_maestro_ac_waveform", **request, signal="/IN")
            second = call("read_maestro_ac_waveform", **request, signal="/OUT")
            assert first["ok"] and second["ok"] and second["sample_count"] == 641, (first, second)
            args = dict(
                input_ref=first["waveform_ref"],
                output_ref=second["waveform_ref"],
                denominator_floor=quantity(1, "mV"),
                recipes=[recipe("bandwidth", result_unit="kHz"), recipe("phase_margin")],
            )
            report = call("measure_ac_response", **args)
            assert report["all_measured"], report
            bandwidth, pm = report["rows"]
            resistance = float(
                next(
                    p["value"]
                    for p in second["source"]["coordinate"]["parameters"]
                    if p["name"] == "RVAL"
                )
            )
            tau = math.sqrt(0.01 * 1e-9)
            damping = resistance * math.sqrt(1e-9 / 0.01)
            x = math.sqrt(
                (
                    2
                    - damping * damping
                    + math.sqrt((damping * damping - 2) ** 2 + 4 * (10**0.3 - 1))
                )
                / 2
            )
            analytic_bw = x / (2 * math.pi * tau)
            unity = math.sqrt(2 - damping * damping)
            analytic_pm = 180 - math.degrees(math.atan2(damping * unity, 1 - unity * unity))
            assert bandwidth["value"] * 1000 == pytest.approx(analytic_bw, rel=0.0005)
            assert pm["value"] == pytest.approx(analytic_pm, abs=0.04)
            native = json.loads(
                bridge.skill(
                    'progn(rspIn=getData("/IN" ?result \'ac ?resultsDir '
                    + json.dumps(first["source"]["results_directory"])
                    + ") "
                    'rspOut=getData("/OUT" ?result \'ac ?resultsDir '
                    + json.dumps(second["source"]["results_directory"])
                    + ") "
                    "rspH=rspOut/rspIn "
                    "aiCircuitJson(list(cross(mag(rspH) pow(10 -.15) 1 'falling) "
                    "phaseMargin(rspH) cross(mag(rspH) 1 1 'falling))))"
                )
            )
            # Native cross uses sampled magnitude; our Cartesian root differs slightly.
            assert bandwidth["value"] * 1000 == pytest.approx(native[0], rel=0.0005)
            assert pm["value"] == pytest.approx(native[1], abs=0.04)
            selected = pm["components"]["selected_crossing"]["frequency_hz"]
            assert selected == pytest.approx(native[2], rel=0.0005)
            direct = json.loads(
                bridge.skill(
                    f"progn(rspAt=value(rspOut {selected!r})/value(rspIn {selected!r}) "
                    "aiCircuitJson(list(mag(rspAt) phaseDeg(rspAt))))"
                )
            )
            assert direct[0] == pytest.approx(1, abs=1e-10)
            assert pm["value"] == pytest.approx(180 + direct[1], abs=1e-10)
            page = call(
                "query_ac_response",
                report_ref=report["report_ref"],
                entity="crossings",
                metric="phase_margin",
            )
            assert page["total"] == 1 and page["items"][0]["direction"] == "falling"
            reports.append(
                dict(
                    point=point,
                    first=first,
                    second=second,
                    args=args,
                    report=report,
                    native=native,
                    analytic=[analytic_bw, analytic_pm],
                    direct=direct,
                )
            )
        absent_args = deepcopy(args)
        absent_args["recipes"][1]["occurrence"] = 2
        absent = call("measure_ac_response", **absent_args)
        assert absent["rows"][1]["status"] == "missing" and absent["rows"][1]["value"] is None
        mismatch = call(
            "measure_ac_response", **dict(args, input_ref=reports[0]["first"]["waveform_ref"])
        )
        assert not mismatch["ok"]
        assert call("inspect_simulation_setup", setup_ref=created["setup_ref"]) == before
        bridge.skill(
            "awvPlotWaveform(awvCreatePlotWindow() list(db20(rspH) phaseDeg(rspH)) "
            '?expr list("Declared RLC loop model magnitude (dB)" "Phase (deg)"))'
        )
        released = call("release_simulation_setup", setup_ref=created["setup_ref"])
        assert released["released"] and released["window_open"]
        assert call("measure_ac_response", **args) == report
        (tmp_path / "acceptance.json").write_text(
            json.dumps(
                dict(
                    created=created,
                    run=run,
                    status=status,
                    reports=reports,
                    absent=absent,
                    mismatch=mismatch,
                    pid=proc.pid,
                    source_setup_unchanged=True,
                    offline_after_release=True,
                ),
                indent=2,
            )
        )
