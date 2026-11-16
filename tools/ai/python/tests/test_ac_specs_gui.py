"""Foreground native AC reports combined under predeclared fixture specifications."""

import json
import math
import os
from copy import deepcopy

import pytest
from ac_fixture import recipe as point_recipe
from ac_response_fixture import recipe as response_recipe
from ac_response_fixture import setup
from template_smic28_support import migration_session
from test_circuit_rc_gui import AI_ROOT, mcp_call
from test_simulation_recipe_failures_gui import terminal
from test_simulation_recipe_gui import load_backend
from test_waveform_measure import quantity

from cadai.mcp import McpServer


def contract_for(expected):
    at = quantity(20, "kHz")
    floor = quantity(1, "mV")
    recipes = {
        "ac_signal": [
            point_recipe("magnitude", "mV", id="absolute", at=at),
            point_recipe("phase", "rad", id="absolute_phase", at=at),
        ],
        "ac_transfer": [
            point_recipe("magnitude", "1", id="gain", at=at),
            point_recipe("magnitude_db", id="gain_db", at=at, reference=quantity(1, "1")),
            point_recipe("phase", id="relative_phase", at=at, magnitude_floor=quantity(0, "1")),
        ],
        "ac_response": [
            response_recipe("bandwidth", result_unit="kHz"),
            response_recipe("phase_margin"),
        ],
    }
    limits = [
        ("V", 1, 4),
        ("deg", 0, 90),
        ("1", 0.8, 2),
        ("dB", -5, 10),
        ("rad", -0.5, 0),
        ("Hz", 70000, 80000),
        ("rad", 0.3, 1.2),
    ]
    specs = []
    for kind, rows in recipes.items():
        for recipe in rows:
            unit, lower, upper = limits[len(specs)]
            specs.append(
                dict(
                    test="small_signal",
                    analysis="ac",
                    kind=kind,
                    recipe=recipe,
                    signals=dict(signal="/OUT")
                    if kind == "ac_signal"
                    else dict(input="/IN", output="/OUT"),
                    unit=unit,
                    lower=lower,
                    upper=upper,
                    **({} if kind == "ac_signal" else dict(denominator_floor=floor)),
                )
            )
    return (
        dict(
            source="Predeclared RLC acceptance limits; never production defaults",
            expected_test_points=expected,
            specifications=specs,
        ),
        recipes,
        floor,
    )


@pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed foreground Spectre acceptance"
)
def test_foreground_ac_specifications(tmp_path):
    with migration_session(tmp_path) as (bridge, server, proc):
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
            task_id="ac_specs",
            presentation="foreground",
            choice_confirmed=True,
        )
        data = setup()
        data["target"]["cell"] = "ac_specifications"
        preview = call("preview_simulation_recipe", recipe=data)
        contract, recipes, floor = contract_for(preview["expected_test_points"])
        # Persist the requested limits/coverage before any simulation starts.
        (tmp_path / "requested-contract.json").write_text(json.dumps(contract, indent=2))
        created = call(
            "create_simulation_setup",
            recipe=data,
            recipe_digest=preview["recipe_digest"],
            task_ref=task["task_ref"],
            request_id="ac_specs",
        )
        assert created["ok"] and created["readback_verified"], created
        before = call("inspect_simulation_setup", setup_ref=created["setup_ref"])
        run = call("run_simulation_setup", setup_ref=created["setup_ref"], request_id="ac_specs")
        status = terminal(server, run)
        assert status["state"] == "completed" and status["progress"] == [4, 4], status
        anchor = call(
            "read_maestro_results", context_ref=created["context_ref"], history=run["history"]
        )
        refs, measurements = [], []
        for point in contract["expected_test_points"]:
            request = dict(
                context_ref=created["context_ref"], history=run["history"], analysis="ac", **point
            )
            first = call("read_maestro_ac_waveform", **request, signal="/IN")
            second = call("read_maestro_ac_waveform", **request, signal="/OUT")
            assert (
                first["source"]["result_ref"]
                == second["source"]["result_ref"]
                == anchor["result_ref"]
            )
            single = call(
                "measure_ac_waveform",
                waveform_ref=second["waveform_ref"],
                recipes=recipes["ac_signal"],
            )
            paired_args = dict(
                input_ref=first["waveform_ref"],
                output_ref=second["waveform_ref"],
                denominator_floor=floor,
            )
            transfer = call("measure_ac_transfer", **paired_args, recipes=recipes["ac_transfer"])
            response = call("measure_ac_response", **paired_args, recipes=recipes["ac_response"])
            assert all(r["all_measured"] for r in (single, transfer, response))
            refs += [single["measurement_ref"], transfer["measurement_ref"], response["report_ref"]]
            measurements.append(
                dict(
                    point=point,
                    first=first,
                    second=second,
                    single=single,
                    transfer=transfer,
                    response=response,
                )
            )
        args = dict(result_ref=anchor["result_ref"], measurement_refs=refs, contract=contract)
        passed = call("evaluate_waveform_specs", **args)
        assert passed["qualification"] == "pass" and passed["counts"]["pass"] == 28, passed
        assert passed["coverage_complete"] and passed["stability_qualified"] is None
        rows = call("query_waveform_spec_report", report_ref=passed["report_ref"])["items"]
        for row in rows:
            raw = row["raw_value"]
            expected = (
                raw / 1000
                if row["metric"] == "absolute"
                else math.degrees(raw)
                if row["metric"] == "absolute_phase"
                else math.radians(raw)
                if row["metric"] in {"relative_phase", "phase_margin"}
                else raw * 1000
                if row["metric"] == "bandwidth"
                else raw
            )
            assert row["value"] == pytest.approx(expected)
        strict = deepcopy(contract)
        strict["specifications"][-1]["lower"] = 0.7
        failed = call("evaluate_waveform_specs", **dict(args, contract=strict))
        assert failed["qualification"] == "fail" and failed["counts"]["fail"] == 2, failed
        failed_rows = call(
            "query_waveform_spec_report", report_ref=failed["report_ref"], status="fail"
        )
        assert all(r["metric"] == "phase_margin" and r["margin"] < 0 for r in failed_rows["items"])
        incomplete = call("evaluate_waveform_specs", **dict(args, measurement_refs=refs[1:]))
        assert incomplete["qualification"] == "incomplete" and incomplete["counts"]["missing"] == 2
        mixed = call(
            "evaluate_waveform_specs", **dict(args, contract=strict, measurement_refs=refs[1:])
        )
        assert mixed["qualification"] == "fail" and not mixed["coverage_complete"]
        branch = deepcopy(contract)
        branch["specifications"][-1]["recipe"]["phase_anchor_turns"] = -1
        mismatch = call("evaluate_waveform_specs", **dict(args, contract=branch))
        assert mismatch["qualification"] == "incomplete" and mismatch["counts"]["error"] == 4
        altered_floor = deepcopy(contract)
        altered_floor["specifications"][-1]["denominator_floor"] = quantity(2, "mV")
        floor_mismatch = call("evaluate_waveform_specs", **dict(args, contract=altered_floor))
        assert floor_mismatch["counts"]["error"] == 4
        assert call("inspect_simulation_setup", setup_ref=created["setup_ref"]) == before
        bridge.skill(
            'progn(aspH=getData("/OUT" ?result \'ac ?resultsDir '
            + json.dumps(second["source"]["results_directory"])
            + ')/getData("/IN" ?result \'ac ?resultsDir '
            + json.dumps(first["source"]["results_directory"])
            + ") awvPlotWaveform(awvCreatePlotWindow() list(db20(aspH) phaseDeg(aspH)) "
            '?expr list("AC specification fixture gain (dB)" "Phase (deg)")))'
        )
        released = call("release_simulation_setup", setup_ref=created["setup_ref"])
        assert released["released"] and released["window_open"]

        class Offline:
            def call(self, *unused):
                pytest.fail("offline qualification contacted Virtuoso")

        restarted = McpServer(Offline(), workspace=tmp_path)
        assert mcp_call(restarted, "evaluate_waveform_specs", args) == passed
        (tmp_path / "acceptance.json").write_text(
            json.dumps(
                dict(
                    created=created,
                    run=run,
                    status=status,
                    measurements=measurements,
                    args=args,
                    passed=passed,
                    failed=failed,
                    incomplete=incomplete,
                    mixed=mixed,
                    mismatch=mismatch,
                    floor_mismatch=floor_mismatch,
                    pid=proc.pid,
                    source_setup_unchanged=True,
                    offline_after_release_and_restart=True,
                ),
                indent=2,
            )
        )
