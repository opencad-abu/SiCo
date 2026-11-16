from __future__ import annotations

from aivworkbench.executors.xcelium import _evaluate_l1_events
from l1_matrix_fixtures import _matrix_event_log, _matrix_event_plan


def test_matrix_evidence_requires_runtime_metadata_and_summary_point_count() -> None:
    plan = _matrix_event_plan()
    accepted = _evaluate_l1_events(plan, _matrix_event_log(plan))
    assert accepted["status"] == "PASS"
    assert accepted["matrix"]["observed_point_count"] == 4

    missing_corner = _matrix_event_log(plan).replace(
        '"corner":{"process":"tt"},', "", 1
    )
    missing_result = _evaluate_l1_events(plan, missing_corner)
    assert missing_result["status"] == "FAIL"
    assert "matrix_evidence_invalid" in {
        item["code"] for item in missing_result["findings"]
    }

    wrong_vector = _matrix_event_log(plan).replace(
        '"vector_id":"negative"', '"vector_id":"unknown"', 1
    )
    wrong_result = _evaluate_l1_events(plan, wrong_vector)
    assert wrong_result["status"] == "FAIL"
    assert "matrix_evidence_invalid" in {
        item["code"] for item in wrong_result["findings"]
    }

    wrong_summary = _matrix_event_log(plan).replace(
        '"matrix_point_count":4', '"matrix_point_count":3'
    )
    summary_result = _evaluate_l1_events(plan, wrong_summary)
    assert summary_result["status"] == "FAIL"
    assert any(
        item["code"] == "summary_mismatch"
        and item["field"] == "matrix_point_count"
        for item in summary_result["findings"]
    )
def test_runtime_matrix_case_set_mismatch_is_stable() -> None:
    plan = _matrix_event_plan()
    log = _matrix_event_log(plan).replace('"id":"matrix-3",', '"id":"unknown",', 1)
    result = _evaluate_l1_events(plan, log)
    codes = {item["code"] for item in result["findings"]}
    assert {"extra_case", "missing_case", "matrix_case_set_mismatch"} <= codes
def test_adapter_rejects_non_object_matrix_contract() -> None:
    from aivworkbench.plugins.xcelium_evidence import get_xcelium_evidence_adapter

    adapter = get_xcelium_evidence_adapter(
        "latched_dynamic_comparator.xcelium_evidence.v1"
    )
    assert adapter is not None
    plan = {"matrix": ["invalid"], "coverage": None, "waveform": None}
    findings = adapter.validate_plan(plan, {})
    assert "xcelium_evidence_matrix_invalid" in {
        item["code"] for item in findings
    }
