from __future__ import annotations

from pathlib import Path

from aivworkbench.executors.xcelium import (
    _evaluate_l1_events,
)
from xcelium_l1_fixtures import (
    _event,
    _passing_log,
    _plan,
    _temporal_log,
    _temporal_plan,
)


def test_l1_event_evaluator_accepts_exact_machine_readable_evidence() -> None:
    result = _evaluate_l1_events(_plan(), _passing_log())
    assert result["status"] == "PASS"
    assert result["case_coverage"] == 1.0
    assert result["passed_assertion_count"] == 2
    assert result["findings"] == []
    assert result["excluded_metrics"] == [
        {"name": "delay", "reason": "calibration_required"}
    ]


def test_l1_event_evaluator_rejects_non_finite_and_wrong_metrics() -> None:
    log = _passing_log().replace('"out":1.2', '"out":NaN')
    result = _evaluate_l1_events(_plan(), log)
    codes = {item["code"] for item in result["findings"]}
    assert "malformed_event" in codes
    assert "missing_case" in codes

    log = _passing_log().replace('"decision":"positive"', '"decision":"negative"')
    result = _evaluate_l1_events(_plan(), log)
    assert "metric_mismatch" in {item["code"] for item in result["findings"]}


def test_l1_event_evaluator_keeps_legacy_plan_compatible() -> None:
    result = _evaluate_l1_events(_plan(), _passing_log())
    assert result["status"] == "PASS"
    assert result["required_temporal_assertions"] == []
    assert result["observed_temporal_assertions"] == []


def test_l1_event_evaluator_requires_coverage_and_waveform_artifacts(
    tmp_path: Path,
) -> None:
    plan = _plan()
    plan["coverage"] = {
        "id": "functional",
        "backend": "ucis",
        "minimum_score": 0.9,
        "required_points": ["positive", "initial-state"],
    }
    plan["waveform"] = {
        "id": "debug-wave",
        "format": "shm",
        "retention": "always",
    }
    coverage = tmp_path / "coverage.ucis"
    waveform = tmp_path / "wave.shm"
    coverage.write_text("ucis evidence", encoding="utf-8")
    waveform.write_text("waveform evidence", encoding="utf-8")
    log = _passing_log()
    log = log.replace(
        _event(
            {
                "event": "summary",
                "status": "PASS",
                "case_count": 1,
                "check_count": 1,
                "assertion_count": 2,
            }
        ),
        _event(
            {
                "event": "coverage",
                "id": "functional",
                "status": "PASS",
                "backend": "ucis",
                "score": 0.95,
                "covered_points": ["positive", "initial-state"],
                "path": "evidence/coverage.ucis",
            }
        )
        + "\n"
        + _event(
            {
                "event": "waveform",
                "id": "debug-wave",
                "status": "PASS",
                "format": "shm",
                "path": "evidence/wave.shm",
            }
        )
        + "\n"
        + _event(
            {
                "event": "summary",
                "status": "PASS",
                "case_count": 1,
                "check_count": 1,
                "assertion_count": 2,
                "coverage_count": 1,
                "waveform_count": 1,
            }
        ),
    )
    payload_root = tmp_path / "payload"
    (payload_root / "evidence").mkdir(parents=True)
    coverage.rename(payload_root / "evidence/coverage.ucis")
    waveform.rename(payload_root / "evidence/wave.shm")
    result = _evaluate_l1_events(plan, log, payload_root)
    assert result["status"] == "PASS"
    assert result["required_coverage_id"] == "functional"
    assert result["required_waveform_id"] == "debug-wave"

    missing = _evaluate_l1_events(plan, _passing_log(), payload_root)
    missing_codes = {item["code"] for item in missing["findings"]}
    assert {"missing_coverage", "missing_waveform", "summary_mismatch"} <= missing_codes

    bad_path = log.replace('"path":"evidence/wave.shm"', '"path":"../wave.shm"')
    bad_path_result = _evaluate_l1_events(plan, bad_path, payload_root)
    assert "waveform_path_invalid" in {
        item["code"] for item in bad_path_result["findings"]
    }

    symlink = payload_root / "evidence/wave-link.shm"
    symlink.symlink_to(waveform)
    linked_log = log.replace(
        '"path":"evidence/wave.shm"', '"path":"evidence/wave-link.shm"'
    )
    linked_result = _evaluate_l1_events(plan, linked_log, payload_root)
    assert "waveform_artifact_symlink" in {
        item["code"] for item in linked_result["findings"]
    }


def test_waveform_retention_policy_is_enforced() -> None:
    plan = _plan()
    plan["waveform"] = {"id": "debug-wave", "format": "shm", "retention": "always"}
    passing = _passing_log().replace(
        _event(
            {
                "event": "summary",
                "status": "PASS",
                "case_count": 1,
                "check_count": 1,
                "assertion_count": 2,
            }
        ),
        _event(
            {
                "event": "waveform",
                "id": "debug-wave",
                "format": "shm",
                "status": "PASS",
                "path": "evidence/waves.shm",
            }
        )
        + "\n"
        + _event(
            {
                "event": "summary",
                "status": "PASS",
                "case_count": 1,
                "check_count": 1,
                "assertion_count": 2,
                "waveform_count": 1,
            }
        ),
    )
    result = _evaluate_l1_events(plan, passing)
    assert result["status"] == "PASS"
    assert result["waveform"]["retention"] == "always"

    for retention, expected_status, expected_code in (
        ("on_failure", "PASS", None),
        ("never", "FAIL", "waveform_retained_when_forbidden"),
    ):
        policy = dict(plan["waveform"])
        policy["retention"] = retention
        policy_plan = dict(plan)
        policy_plan["waveform"] = policy
        policy_log = passing
        if retention == "on_failure":
            policy_log = "\n".join(
                line
                for line in policy_log.splitlines()
                if '"event":"waveform"' not in line
            )
            policy_log = policy_log.replace(
                '"assertion_count":2,"case_count":1,"check_count":1',
                '"assertion_count":2,"case_count":1,"check_count":1,"waveform_count":0',
            )
            policy_log = policy_log.replace('"waveform_count":1', '"waveform_count":0')
        policy_result = _evaluate_l1_events(policy_plan, policy_log)
        assert policy_result["status"] == expected_status
        if expected_code:
            assert expected_code in {item["code"] for item in policy_result["findings"]}

    missing_on_failure_log = _passing_log().replace(
        _event(
            {
                "event": "summary",
                "status": "PASS",
                "case_count": 1,
                "check_count": 1,
                "assertion_count": 2,
            }
        ),
        _event(
            {
                "event": "summary",
                "status": "PASS",
                "case_count": 1,
                "check_count": 1,
                "assertion_count": 2,
                "waveform_count": 0,
            }
        ),
    )
    missing_on_failure = _evaluate_l1_events(
        {**plan, "waveform": {**plan["waveform"], "retention": "on_failure"}},
        missing_on_failure_log,
    )
    assert missing_on_failure["status"] == "PASS"


def test_l1_event_evaluator_rejects_duplicate_and_missing_evidence() -> None:
    duplicated = (
        _passing_log()
        + "\n"
        + _event(
            {
                "event": "case",
                "id": "positive",
                "type": "positive_differential",
                "region": "deterministic",
                "status": "PASS",
                "passed_assertions": ["decision", "out"],
                "metrics": {"decision": "positive", "out": 1.2},
            }
        )
    )
    result = _evaluate_l1_events(_plan(), duplicated)
    assert result["status"] == "FAIL"
    assert "duplicate_event" in {item["code"] for item in result["findings"]}

    result = _evaluate_l1_events(_plan(), "")
    codes = {item["code"] for item in result["findings"]}
    assert result["status"] == "FAIL"
    assert {"missing_case", "missing_check", "summary_count_mismatch"} <= codes


def test_l1_event_evaluator_requires_case_metadata_to_match_exactly() -> None:
    plan = _plan()
    plan["cases"][0]["corner"] = {"process": "tt", "vdd": 1.2}
    plan["cases"][0]["vector_id"] = "tt-001"
    result = _evaluate_l1_events(plan, _passing_log())
    codes = {item["code"] for item in result["findings"]}
    assert result["status"] == "FAIL"
    assert "case_metadata_mismatch" in codes

    with_metadata = _passing_log().replace(
        '"metrics":{"decision":"positive","out":1.2}',
        '"corner":{"process":"tt","vdd":1.2},"metrics":{"decision":"positive","out":1.2},"vector_id":"tt-001"',
    )
    accepted = _evaluate_l1_events(plan, with_metadata)
    assert accepted["status"] == "PASS"


def test_l1_event_evaluator_requires_exact_temporal_evidence() -> None:
    result = _evaluate_l1_events(_temporal_plan(), _temporal_log())
    assert result["status"] == "PASS"
    assert result["required_temporal_assertions"] == ["output_complementary"]
    assert result["observed_temporal_assertions"] == ["output_complementary"]
    assert result["temporal_assertion_count"] == 1

    missing = _temporal_log().replace(
        _event({"event": "temporal", "id": "output_complementary", "status": "PASS"})
        + "\n",
        "",
    )
    missing_result = _evaluate_l1_events(_temporal_plan(), missing)
    assert "missing_temporal_assertion" in {
        item["code"] for item in missing_result["findings"]
    }

    failed = _evaluate_l1_events(_temporal_plan(), _temporal_log("FAIL"))
    assert "temporal_assertion_not_pass" in {
        item["code"] for item in failed["findings"]
    }

    extra = (
        _temporal_log()
        + "\n"
        + _event({"event": "temporal", "id": "unplanned_property", "status": "PASS"})
    )
    extra_result = _evaluate_l1_events(_temporal_plan(), extra)
    assert "extra_temporal_assertion" in {
        item["code"] for item in extra_result["findings"]
    }

    duplicate = (
        _temporal_log()
        + "\n"
        + _event({"event": "temporal", "id": "output_complementary", "status": "PASS"})
    )
    duplicate_result = _evaluate_l1_events(_temporal_plan(), duplicate)
    assert "duplicate_event" in {item["code"] for item in duplicate_result["findings"]}
