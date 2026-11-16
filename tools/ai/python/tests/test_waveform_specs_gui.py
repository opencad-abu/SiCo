"""Foreground RC sweep: single/pair measurement qualification with deliberate failures."""

import json
import os
from copy import deepcopy

import pytest
from template_smic28_support import migration_session
from test_circuit_rc_gui import AI_ROOT, mcp_call
from test_simulation_recipe_failures_gui import terminal
from test_simulation_recipe_gui import load_backend
from test_waveform_measure import quantity
from test_waveform_measure import recipe as single_recipe
from waveform_pair_fixture import measurement_recipes, setup_recipe


def acceptance_contract(expected):
    pair = measurement_recipes()
    single = single_recipe("sample", at=quantity(2.001, "us"), result_unit="mV")
    specs = [
        dict(
            test="transient_response",
            analysis="tran",
            kind="pair",
            signals=dict(input="/IN", output="/OUT"),
            recipe=r,
            unit="us" if r["id"] == "delay" else "1",
            lower=0.6 if r["id"] == "delay" else 0.35,
            upper=1.5 if r["id"] == "delay" else 0.7,
        )
        for r in pair
    ]
    specs.append(
        dict(
            test="transient_response",
            analysis="tran",
            kind="single",
            signals=dict(signal="/OUT"),
            recipe=single,
            unit="V",
            lower=0.35,
            upper=0.7,
        )
    )
    return (
        dict(
            source="Explicit RC fixture limits; coverage from requested recipe preview",
            expected_test_points=expected,
            specifications=specs,
        ),
        pair,
        single,
    )


@pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1", reason="licensed foreground Spectre acceptance"
)
def test_waveform_spec_qualification(tmp_path):
    with migration_session(tmp_path) as (bridge, server, proc):
        bridge.skill('ddCreateLib("genericWork" ' + json.dumps(str(tmp_path / "library")) + ")")
        load_backend(bridge, tmp_path)
        for module in ["skill/AI_maestro_waveform.il", "python/tests/waveform_pair_fixture.il"]:
            bridge.skill("load(" + json.dumps(str(AI_ROOT / module)) + ")")
        assert bridge.skill("pairFixtureCreate()") == "t"

        def call(name, **args):
            return mcp_call(server, name, args)

        task = call(
            "begin_circuit_task", task_id="specs", presentation="foreground", choice_confirmed=True
        )
        recipe = setup_recipe()
        recipe["target"]["cell"] = "waveform_specs"
        preview = call("preview_simulation_recipe", recipe=recipe)
        # Expected coverage is fixed before execution, never derived from observed RDB rows.
        contract, pair, single = acceptance_contract(preview["expected_test_points"])
        setup = call(
            "create_simulation_setup",
            recipe=recipe,
            recipe_digest=preview["recipe_digest"],
            task_ref=task["task_ref"],
            request_id="specs",
        )
        assert setup["ok"] and setup["saved"] and setup["readback_verified"], setup
        before = call("inspect_simulation_setup", setup_ref=setup["setup_ref"])
        assert before["ok"], before
        run = call("run_simulation_setup", setup_ref=setup["setup_ref"], request_id="specs")
        status = terminal(server, run)
        assert status["state"] == "completed" and status["progress"] == [4, 4], status
        anchor = call(
            "read_maestro_results", context_ref=setup["context_ref"], history=run["history"]
        )
        measurements, refs = [], []
        for point in contract["expected_test_points"]:
            args = dict(
                context_ref=setup["context_ref"], history=run["history"], analysis="tran", **point
            )
            first = call("read_maestro_waveform", **args, signal="/IN")
            second = call("read_maestro_waveform", **args, signal="/OUT")
            assert (
                first["source"]["result_ref"]
                == second["source"]["result_ref"]
                == anchor["result_ref"]
            )
            paired = call(
                "measure_waveform_pair",
                input_ref=first["waveform_ref"],
                output_ref=second["waveform_ref"],
                recipes=pair,
            )
            sampled = call(
                "measure_waveform", waveform_ref=second["waveform_ref"], recipes=[single]
            )
            assert paired["all_measured"] and sampled["all_measured"], (paired, sampled)
            refs.extend([paired["measurement_ref"], sampled["measurement_ref"]])
            measurements.append(dict(point=point, pair=paired, single=sampled))
        evaluate_args = dict(
            result_ref=anchor["result_ref"], measurement_refs=refs, contract=contract
        )
        passed = call("evaluate_waveform_specs", **evaluate_args)
        assert passed["qualification"] == "pass" and passed["coverage_complete"], passed
        assert passed["counts"] == dict(zip(["pass", "fail", "missing", "error"], [16, 0, 0, 0]))
        rows = call("query_waveform_spec_report", report_ref=passed["report_ref"])["items"]
        for row in rows:
            factor = 1e-3 if row["metric"] in {"delay", "sample"} else 1
            assert row["value"] == pytest.approx(row["raw_value"] * factor)

        strict = deepcopy(contract)
        strict["specifications"][0]["upper"] = 1.0
        failed = call("evaluate_waveform_specs", **dict(evaluate_args, contract=strict))
        assert failed["qualification"] == "fail" and failed["coverage_complete"], failed
        assert failed["counts"]["fail"] == 2
        failed_rows = call(
            "query_waveform_spec_report", report_ref=failed["report_ref"], status="fail"
        )
        assert failed_rows["total"] == 2
        assert {r["corner"] for r in failed_rows["items"]} == {"cool", "warm"}
        for row in failed_rows["items"]:
            assert row["metric"] == "delay" and row["margin"] < 0 and row["point"] == 2
            parameters = row["sources"]["input"]["coordinate"]["parameters"]
            assert float(next(p["value"] for p in parameters if p["name"] == "RVAL")) == 2000
        worst = failed["summaries"][0]["worst"]
        assert worst in failed_rows["items"] and worst["value"] == pytest.approx(1.3859, abs=0.002)

        missing = call("evaluate_waveform_specs", **dict(evaluate_args, measurement_refs=refs[:-1]))
        assert missing["qualification"] == "incomplete" and missing["counts"]["missing"] == 1
        mixed = call(
            "evaluate_waveform_specs",
            **dict(evaluate_args, measurement_refs=refs[:-1], contract=strict),
        )
        assert mixed["qualification"] == "fail" and not mixed["coverage_complete"]
        assert mixed["counts"]["fail"] == 2 and mixed["counts"]["missing"] == 1
        mismatch = deepcopy(contract)
        mismatch["specifications"][-1]["recipe"]["at"]["value"] = 2.1
        mismatched = call("evaluate_waveform_specs", **dict(evaluate_args, contract=mismatch))
        assert mismatched["qualification"] == "incomplete" and mismatched["counts"]["error"] == 4
        errors = call(
            "query_waveform_spec_report", report_ref=mismatched["report_ref"], status="error"
        )
        assert all(r["reason"] == "measurement_recipe_mismatch" for r in errors["items"])
        after = call("inspect_simulation_setup", setup_ref=setup["setup_ref"])
        assert after["ok"], after

        bridge.skill(
            'progn(specIn=getData("/IN" ?result \'tran ?resultsDir '
            + json.dumps(first["source"]["results_directory"])
            + ") "
            'specOut=getData("/OUT" ?result \'tran ?resultsDir '
            + json.dumps(second["source"]["results_directory"])
            + ") "
            'awvPlotWaveform(awvCreatePlotWindow() list(specIn specOut) ?expr list("IN" "OUT")))'
        )
        released = call("release_simulation_setup", setup_ref=setup["setup_ref"])
        assert released["released"] and released["window_open"], released
        assert call("evaluate_waveform_specs", **evaluate_args) == passed
        assert (
            call("query_waveform_spec_report", report_ref=failed["report_ref"], status="fail")
            == failed_rows
        )
        (tmp_path / "acceptance.json").write_text(
            json.dumps(
                dict(
                    setup=setup,
                    run=run,
                    status=status,
                    anchor=anchor,
                    contract=contract,
                    measurements=measurements,
                    passed=passed,
                    failed=failed,
                    missing=missing,
                    mixed=mixed,
                    mismatched=mismatched,
                    failed_rows=failed_rows,
                    before=before,
                    after=after,
                    released=released,
                    pid=proc.pid,
                    offline_after_release=True,
                ),
                indent=2,
            )
        )
