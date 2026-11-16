"""Specification coverage, recipe identity, unit conversion and failure provenance."""

from copy import deepcopy

import pytest
from test_circuit_rc_gui import mcp_call
from waveform_spec_fixture import fixture

from cadai.mcp import McpServer
from cadai.result_contract import ResultError
from cadai.waveform_specs import call_waveform_specs


def evaluate(path, args):
    return call_waveform_specs("evaluate_waveform_specs", args, workspace=path)


def mutate_report(store, args, index, change):
    value = store.get(args["measurement_refs"][index])
    change(value)
    prefix = (
        "wave_measurements"
        if value["schema"] == "cad.waveform.measurements.v1"
        else "pair_measurements"
    )
    ref, _ = store.put(prefix, value)
    args["measurement_refs"][index] = ref


def test_mixed_reports_units_bounds_worst_and_restart(tmp_path):
    store, args, _, _ = fixture(tmp_path)
    report = evaluate(tmp_path, args)
    assert report["qualification"] == "pass" and report["coverage_complete"]
    assert report["counts"] == {"pass": 4, "fail": 0, "missing": 0, "error": 0}
    full = store.get(report["report_ref"])
    assert [(r["value"], r["raw_value"], r["raw_unit"], r["unit"]) for r in full["rows"]] == [
        (0.5, 500, "mV", "V")
    ] * 2 + [(-0.5, -500, "ms", "s")] * 2
    assert report["summaries"][1]["worst"]["point"] == 1
    assert full["rows"][2]["components"]["input"]["unit"] == "s"
    assert full["rows"][0]["sources"]["signal"]["coordinate"]["parameters"][0]["name"] == "vdd"
    args["measurement_refs"].reverse()
    assert evaluate(tmp_path, args) == report

    class Offline:
        def call(self, *unused):
            pytest.fail("spec evaluation must not dispatch to Virtuoso")

    assert (
        mcp_call(McpServer(Offline(), workspace=tmp_path), "evaluate_waveform_specs", args)
        == report
    )
    page = call_waveform_specs(
        "query_waveform_spec_report",
        dict(report_ref=report["report_ref"], corner="hot", limit=1),
        workspace=tmp_path,
    )
    assert page["total"] == 2 and page["next_offset"] == 1
    assert (
        call_waveform_specs(
            "query_waveform_spec_report",
            dict(report_ref=report["report_ref"], status="fail"),
            workspace=tmp_path,
        )["total"]
        == 0
    )
    with pytest.raises(ResultError):
        call_waveform_specs(
            "query_waveform_spec_report",
            dict(report_ref=report["report_ref"], entity="coordinates", metric="sample"),
            workspace=tmp_path,
        )


def test_known_failure_with_missing_preserves_failure_and_coverage(tmp_path):
    store, args, _, _ = fixture(tmp_path)
    args["contract"]["specifications"][1]["upper"] = -0.55
    args["measurement_refs"].pop(0)
    report = evaluate(tmp_path, args)
    assert report["qualification"] == "fail" and report["spec_qualified"] is False
    assert not report["coverage_complete"] and report["counts"]["missing"] == 1
    assert report["counts"]["fail"] == 2
    row = next(r for r in store.get(report["report_ref"])["rows"] if r["status"] == "missing")
    assert row["reason"] == "measurement_missing" and row["value"] is None
    assert report["summaries"][1]["worst"]["margin"] == pytest.approx(-0.05)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        (lambda s: s["recipe"]["at"].update(value=0.7), "measurement_recipe_mismatch"),
        (lambda s: s["signals"].update(signal="/wrong"), "measurement_source_mismatch"),
        (lambda s: s.update(analysis="ac"), "measurement_source_mismatch"),
        (lambda s: s.update(unit="A"), "unit_incompatible"),
    ],
)
def test_matching_metric_id_is_insufficient(tmp_path, mutation, reason):
    store, args, _, _ = fixture(tmp_path)
    mutation(args["contract"]["specifications"][0])
    report = evaluate(tmp_path, args)
    assert report["qualification"] == "incomplete" and report["spec_qualified"] is None
    assert report["counts"]["error"] == 2
    assert store.get(report["report_ref"])["rows"][0]["reason"] == reason


@pytest.mark.parametrize(
    "status,reason", [("missing", "output:no_crossing"), ("error", "denominator_at_or_below_floor")]
)
def test_measurement_failure_reason_preserved(tmp_path, status, reason):
    store, args, _, _ = fixture(tmp_path)
    mutate_report(
        store, args, 1, lambda r: r["rows"][0].update(status=status, reason=reason, value=None)
    )
    report = evaluate(tmp_path, args)
    assert report["counts"][status] == 1 and not report["coverage_complete"]
    row = next(r for r in store.get(report["report_ref"])["rows"] if r["status"] == status)
    assert row["reason"] == reason == row["measurement_reason"]
    assert row["value"] is None and row["measurement_ref"] == args["measurement_refs"][1]


@pytest.mark.parametrize("inclusive,expected", [(True, "pass"), (False, "fail")])
def test_equal_boundary_flags(tmp_path, inclusive, expected):
    _, args, _, _ = fixture(tmp_path)
    args["contract"]["specifications"][0].update(lower=0.5, lower_inclusive=inclusive)
    report = evaluate(tmp_path, args)
    assert report["qualification"] == expected
    assert report["summaries"][0]["worst"]["margin"] == 0


def test_missing_point_unexpected_point_and_empty_reports(tmp_path):
    _, args, _, _ = fixture(tmp_path)
    args["measurement_refs"] = []
    report = evaluate(tmp_path, args)
    assert report["counts"]["missing"] == 4 and report["qualification"] == "incomplete"
    args["contract"]["expected_test_points"] = [
        dict(test="test", corner="cold", point=1),
        dict(test="test", corner="cold", point=99),
    ]
    report = evaluate(tmp_path, args)
    assert report["missing_test_points"] == [("test", "cold", 99)]
    assert report["unexpected_test_points"] == [("test", "hot", 2)]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["source"].update(history="H.1"),
        lambda r: r["source"].update(result_ref="results_" + "b" * 64),
        lambda r: r["source"]["coordinate"].update(parameters=[]),
        lambda r: r["rows"][0].update(value=True),
        lambda r: r["rows"][0].update(unit="V"),
        lambda r: r["rows"].clear(),
        lambda r: r["recipes"].clear(),
        lambda r: r["rows"][0].update(status="missing", value=None, reason=None),
    ],
)
def test_corrupt_or_wrong_history_reports_refused(tmp_path, mutation):
    store, args, _, _ = fixture(tmp_path)
    mutate_report(store, args, 0, mutation)
    with pytest.raises(ResultError):
        evaluate(tmp_path, args)


def test_duplicates_are_not_overwritten(tmp_path):
    store, args, _, _ = fixture(tmp_path)
    args["measurement_refs"].append(args["measurement_refs"][0])
    with pytest.raises(ResultError):
        evaluate(tmp_path, args)
    args["measurement_refs"].pop()
    other = store.get(args["measurement_refs"][0])
    other["recipes"][0]["at"]["value"] = 0.7
    ref, _ = store.put("wave_measurements", other)
    args["measurement_refs"].append(ref)
    with pytest.raises(ResultError):
        evaluate(tmp_path, args)


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c["expected_test_points"].append(c["expected_test_points"][0]),
        lambda c: c["specifications"][0].update(lower=2, upper=1),
        lambda c: c["specifications"][0].update(upper=float("nan")),
        lambda c: c["specifications"][0]["signals"].update(input="/input"),
        lambda c: c["specifications"][0]["recipe"].update(interpolation="nearest"),
        lambda c: c["specifications"].append(deepcopy(c["specifications"][0])),
    ],
)
def test_contract_validation(tmp_path, change):
    _, args, _, _ = fixture(tmp_path)
    change(args["contract"])
    with pytest.raises(ResultError):
        evaluate(tmp_path, args)


def test_digest_guard(tmp_path):
    store, args, _, _ = fixture(tmp_path)
    path = store.directory() / (args["measurement_refs"][0] + ".json")
    path.write_text("{}")
    with pytest.raises(ResultError):
        evaluate(tmp_path, args)


def test_unfinished_anchor_test_is_not_qualified(tmp_path):
    store, args, _, _ = fixture(tmp_path, first_status="error")
    report = evaluate(tmp_path, args)
    assert report["qualification"] == "incomplete" and report["counts"]["missing"] == 2
    rows = store.get(report["report_ref"])["rows"]
    assert all(r["reason"] == "test_not_done" for r in rows if r["corner"] == "cold")


def test_unselected_metrics_do_not_expand_qualification(tmp_path):
    _, args, _, _ = fixture(tmp_path)
    args["contract"]["specifications"].pop()
    report = evaluate(tmp_path, args)
    assert report["qualification"] == "pass" and report["counts"]["pass"] == 2
    assert report["unselected_measurements"] == 2


def test_conversion_overflow_remains_an_error_row(tmp_path):
    store, args, _, _ = fixture(tmp_path)
    mutate_report(store, args, 0, lambda r: r["rows"][0].update(value=9e299))
    args["contract"]["specifications"][0]["unit"] = "fV"
    report = evaluate(tmp_path, args)
    row = store.get(report["report_ref"])["rows"][0]
    assert row["status"] == "error" and row["reason"] == "numeric_overflow"
    assert row["value"] is None and row["raw_value"] == 9e299


def test_pair_source_analysis_mismatch_is_refused(tmp_path):
    store, args, _, _ = fixture(tmp_path)
    mutate_report(store, args, 1, lambda r: r["sources"]["output"].update(analysis="dc"))
    with pytest.raises(ResultError, match="pair report source mismatch"):
        evaluate(tmp_path, args)


def test_report_row_budget_is_checked_before_measurements(tmp_path):
    _, args, _, _ = fixture(tmp_path)
    args["contract"]["expected_test_points"] = [
        dict(test="test", corner="cold", point=p) for p in range(2048)
    ]
    extra = deepcopy(args["contract"]["specifications"][0])
    extra["recipe"]["id"] = "third"
    args["contract"]["specifications"].append(extra)
    with pytest.raises(ResultError, match="4096 measurements"):
        evaluate(tmp_path, args)
