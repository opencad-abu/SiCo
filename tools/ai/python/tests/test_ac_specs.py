"""AC contract identity, angle units, coverage and offline MCP regression."""

import math

import pytest
from ac_spec_fixture import fixture
from test_circuit_rc_gui import mcp_call
from test_waveform_measure import quantity, recipe
from test_waveform_specs import evaluate

from cadai.ac_response import call_response
from cadai.ac_tools import call_ac
from cadai.mcp import McpServer
from cadai.result_contract import ResultError
from cadai.waveform_tools import call_waveform


def test_combined_ac_units_provenance_offline_and_paging(tmp_path):
    store, args, _, _ = fixture(tmp_path)
    report = evaluate(tmp_path, args)
    assert report["qualification"] == "pass" and report["counts"]["pass"] == 14
    assert report["stability_qualified"] is None and report["coverage_complete"]
    full = store.get(report["report_ref"])
    phase = next(r for r in full["rows"] if r["metric"] == "relative_phase")
    assert phase["value"] == pytest.approx(math.degrees(phase["raw_value"]))
    assert (phase["raw_unit"], phase["unit"]) == ("rad", "deg")
    assert phase["components"]["phasor"]["unit"] == "1"
    pm = next(r for r in full["rows"] if r["metric"] == "phase_margin")
    assert pm["value"] == pytest.approx(math.radians(pm["raw_value"]))
    assert pm["sources"]["output"]["coordinate"]["point"] == pm["point"]
    assert pm["components"]["selected_crossing"]["frequency_hz"] == pytest.approx(65.555555556)
    assert all(s["measurement_identity_sha256"] for s in report["summaries"])

    class Offline:
        def call(self, *unused):
            pytest.fail("qualification must not contact Virtuoso")

    server = McpServer(Offline(), workspace=tmp_path)
    args["measurement_refs"].reverse()
    assert mcp_call(server, "evaluate_waveform_specs", args) == report
    page = mcp_call(
        server,
        "query_waveform_spec_report",
        dict(report_ref=report["report_ref"], corner="case_13", limit=3),
    )
    assert page["total"] == 7 and len(page["items"]) == 3 and page["next_offset"] == 3


@pytest.mark.parametrize(
    "index,change,reason",
    [
        (
            2,
            lambda s: s.update(denominator_floor=quantity(0.01, "V")),
            "measurement_denominator_floor_mismatch",
        ),
        (
            3,
            lambda s: s["recipe"].update(reference=quantity(1, "1")),
            "measurement_recipe_mismatch",
        ),
        (4, lambda s: s["recipe"].update(at=quantity(70, "Hz")), "measurement_recipe_mismatch"),
        (5, lambda s: s["recipe"].update(drop_db=3), "measurement_recipe_mismatch"),
        (5, lambda s: s["recipe"].update(occurrence=2), "measurement_recipe_mismatch"),
        (6, lambda s: s["recipe"].update(loop_sign=-1), "measurement_recipe_mismatch"),
        (6, lambda s: s["recipe"].update(phase_anchor_turns=-1), "measurement_recipe_mismatch"),
        (6, lambda s: s["recipe"].update(direction="rising"), "measurement_recipe_mismatch"),
        (6, lambda s: s["signals"].update(output="/other"), "measurement_source_mismatch"),
        (
            2,
            lambda s: (
                s.update(kind="ac_signal", signals=dict(signal="/probe/drive")),
                s.pop("denominator_floor"),
            ),
            "measurement_source_mismatch",
        ),
        (3, lambda s: s.update(unit="1"), "unit_incompatible"),
        (6, lambda s: s.update(unit="V"), "unit_incompatible"),
    ],
)
def test_ac_identity_is_more_than_id(tmp_path, index, change, reason):
    store, args, _, _ = fixture(tmp_path)
    change(args["contract"]["specifications"][index])
    report = evaluate(tmp_path, args)
    assert report["qualification"] == "incomplete" and report["counts"]["error"] == 2
    errors = [r for r in store.get(report["report_ref"])["rows"] if r["status"] == "error"]
    assert all(r["reason"] == reason and r["value"] is None for r in errors)


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.pop("denominator_floor"),
        lambda s: s.update(denominator_floor=quantity(-1, "V")),
        lambda s: s.update(analysis="tran"),
        lambda s: s["recipe"].update(phase_mode="principal"),
        lambda s: s["recipe"].update(characteristic="one_minus_L"),
        lambda s: s["recipe"].update(phase_anchor_turns=0.5),
        lambda s: s["recipe"].update(occurrence=257),
    ],
)
def test_incomplete_or_unsupported_contract_refused(tmp_path, change):
    _, args, _, _ = fixture(tmp_path)
    change(args["contract"]["specifications"][-1])
    with pytest.raises(ResultError):
        evaluate(tmp_path, args)


def test_ac_missing_error_and_known_failure_precedence(tmp_path):
    store, args, _, waves = fixture(tmp_path)
    args["measurement_refs"].pop(0)
    incomplete = evaluate(tmp_path, args)
    assert incomplete["qualification"] == "incomplete" and incomplete["counts"]["missing"] == 2
    args["contract"]["specifications"][2]["lower"] = 1.15
    failed = evaluate(tmp_path, args)
    assert failed["qualification"] == "fail" and failed["counts"]["fail"] == 2
    assert not failed["coverage_complete"] and failed["spec_qualified"] is False
    pm = args["contract"]["specifications"][-1]
    pm["recipe"]["occurrence"] = 2
    response = call_response(
        "measure_ac_response",
        dict(
            input_ref=waves[0][0],
            output_ref=waves[0][1],
            recipes=[s["recipe"] for s in args["contract"]["specifications"][-2:]],
            denominator_floor=pm["denominator_floor"],
        ),
        workspace=tmp_path,
    )
    args["measurement_refs"][1] = response["report_ref"]
    full = store.get(evaluate(tmp_path, args)["report_ref"])
    row = next(r for r in full["rows"] if r["metric"] == "phase_margin" and r["point"] == 7)
    assert row["reason"] == "requested_crossing_not_found" and row["status"] == "missing"
    assert row["value"] is None and row["raw_value"] is None


def test_generated_error_and_negative_phase_branch_are_preserved(tmp_path):
    store, args, _, waves = fixture(tmp_path)
    specs = args["contract"]["specifications"]
    for spec in specs[2:5]:
        spec["denominator_floor"] = quantity(3, "V")
    error = call_ac(
        "measure_ac_transfer",
        dict(
            input_ref=waves[0][0],
            output_ref=waves[0][1],
            denominator_floor=quantity(3, "V"),
            recipes=[s["recipe"] for s in specs[2:5]],
        ),
        workspace=tmp_path,
        client=None,
    )
    args["measurement_refs"][1] = error["measurement_ref"]
    full = store.get(evaluate(tmp_path, args)["report_ref"])
    row = next(r for r in full["rows"] if r["metric"] == "ratio" and r["point"] == 7)
    assert row["reason"] == "denominator_at_or_below_floor" and row["status"] == "error"
    specs[-1]["recipe"]["phase_anchor_turns"] = -1
    specs[-1].update(lower=-6, upper=-5)
    for i, (ar, br) in enumerate(waves):
        response = call_response(
            "measure_ac_response",
            dict(
                input_ref=ar,
                output_ref=br,
                denominator_floor=specs[-1]["denominator_floor"],
                recipes=[s["recipe"] for s in specs[-2:]],
            ),
            workspace=tmp_path,
        )
        args["measurement_refs"][i * 3 + 2] = response["report_ref"]
    full = store.get(evaluate(tmp_path, args)["report_ref"])
    rows = [r for r in full["rows"] if r["metric"] == "phase_margin"]
    assert all(r["status"] == "pass" and r["raw_value"] < -300 and r["value"] < -5 for r in rows)


@pytest.mark.parametrize("inclusive,verdict", [(True, "pass"), (False, "fail")])
def test_ac_exact_boundary(tmp_path, inclusive, verdict):
    _, args, _, _ = fixture(tmp_path)
    args["contract"]["specifications"][1].update(upper=-math.pi / 2, upper_inclusive=inclusive)
    report = evaluate(tmp_path, args)
    assert report["qualification"] == verdict and report["summaries"][1]["worst"]["margin"] == 0


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r["sources"]["output"].update(history="Other.0"),
        lambda r: r["sources"]["input"]["coordinate"].update(parameters=[]),
        lambda r: r["sources"]["output"].update(analysis="dc"),
        lambda r: r["sources"].pop("input"),
        lambda r: r["rows"][0].update(unit="Hz"),
        lambda r: r["rows"][0].update(value=True),
        lambda r: r.pop("denominator_floor"),
    ],
)
def test_corrupt_ac_report_refused(tmp_path, change):
    store, args, _, _ = fixture(tmp_path)
    original = store.get(args["measurement_refs"][2])
    change(original)
    args["measurement_refs"][2], _ = store.put("ac_response", original)
    with pytest.raises(ResultError):
        evaluate(tmp_path, args)


def test_duplicate_ac_metrics_refused(tmp_path):
    store, args, _, _ = fixture(tmp_path)
    report = store.get(args["measurement_refs"][2])
    report["recipes"][0]["drop_db"] = 3
    ref, _ = store.put("ac_response", report)
    args["measurement_refs"].append(ref)
    with pytest.raises(ResultError, match="ambiguous duplicate"):
        evaluate(tmp_path, args)


def test_real_and_ac_can_share_one_explicit_contract(tmp_path):
    store, args, _, waves = fixture(tmp_path)
    r = recipe("sample", at=quantity(0.5, "s"), result_unit="V")
    for ar, _ in waves:
        w = store.get(ar)
        w.update(schema="cad.maestro.waveform.v1", x_unit="s", samples=[[0, 1], [1, 2]])
        w["source"]["analysis"] = "tran"
        ref, _ = store.put("waveform", w)
        report = call_waveform(
            "measure_waveform", dict(waveform_ref=ref, recipes=[r]), workspace=tmp_path, client=None
        )
        args["measurement_refs"].append(report["measurement_ref"])
    args["contract"]["specifications"].append(
        dict(
            test="probe",
            analysis="tran",
            kind="single",
            signals=dict(signal="/probe/drive"),
            recipe=r,
            unit="V",
            lower=1,
            upper=2,
        )
    )
    report = evaluate(tmp_path, args)
    assert report["qualification"] == "pass" and report["counts"]["pass"] == 16
