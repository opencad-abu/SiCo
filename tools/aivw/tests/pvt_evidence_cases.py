from __future__ import annotations

from pathlib import Path


from aivworkbench.pvt_adapter import (
    build_physical_pvt_plan,
    evaluate_physical_pvt_results,
)
from pvt_adapter_fixtures import (_contract, _cases, _setup, _registry, _materialize_observations)


def test_raw_evidence_evaluator_requires_exact_points_and_keeps_verdict_unestablished(tmp_path: Path) -> None:
    model_root, payload_root, deck_root, tools = _setup(tmp_path)
    plan = build_physical_pvt_plan(
        _contract(), registry=_registry(), tools=tools, model_root=model_root,
        payload_root=payload_root, deck_root=deck_root, cases=_cases()
    )
    observations = _materialize_observations(plan, payload_root)
    result = evaluate_physical_pvt_results(
        _contract(), observations, payload_root=payload_root, producer="spectre_pvt_test_v1@contract-v1", cases=_cases()
    )
    assert result["status"] == "PASS"
    assert result["code"] == "pvt_raw_evidence_verified"
    assert result["expected_point_count"] == result["observed_point_count"] == 4
    assert result["design_verdict"] == "NOT_ESTABLISHED"
    assert result["metrics_evaluation"] == "not_performed"
    assert len(result["artifact_locators"]) == 4

    from aivworkbench.manifest import artifact_locator_index

    manifest_locators = artifact_locator_index(
        payload_root, result["artifact_locators"]
    )
    assert len(manifest_locators) == 4
    assert all(item["exists"] is True for item in manifest_locators)

    missing = evaluate_physical_pvt_results(
        _contract(), observations[:-1], payload_root=payload_root,
        producer="spectre_pvt_test_v1@contract-v1", cases=_cases()
    )
    assert missing["status"] == "BLOCKED_EVIDENCE"
    assert any(item["code"] == "pvt_observation_missing_points" for item in missing["findings"])


def test_raw_evidence_rejects_path_mismatch_symlink_and_nonfinite_metric(tmp_path: Path) -> None:
    model_root, payload_root, deck_root, tools = _setup(tmp_path)
    plan = build_physical_pvt_plan(
        _contract(), registry=_registry(), tools=tools, model_root=model_root,
        payload_root=payload_root, deck_root=deck_root, cases=_cases()
    )
    observations = _materialize_observations(plan, payload_root)
    observations[0]["raw_output"] = "../outside"
    observations[1]["metrics"] = {"value": float("nan")}
    result = evaluate_physical_pvt_results(
        _contract(), observations, payload_root=payload_root,
        producer="spectre_pvt_test_v1@contract-v1", cases=_cases()
    )
    codes = {item["code"] for item in result["findings"]}
    assert result["status"] == "BLOCKED_EVIDENCE"
    assert "pvt_raw_output_path_invalid" in codes
    assert "pvt_point_metrics_non_finite" in codes


def test_raw_evidence_rejects_coordinate_and_section_mismatch(tmp_path: Path) -> None:
    model_root, payload_root, deck_root, tools = _setup(tmp_path)
    plan = build_physical_pvt_plan(
        _contract(), registry=_registry(), tools=tools, model_root=model_root,
        payload_root=payload_root, deck_root=deck_root, cases=_cases()
    )
    observations = _materialize_observations(plan, payload_root)
    observations[0]["coordinates"]["vdd"] = 9.9
    observations[2]["model_section"] = "tt"
    result = evaluate_physical_pvt_results(
        _contract(), observations, payload_root=payload_root,
        producer="spectre_pvt_test_v1@contract-v1", cases=_cases()
    )
    codes = {item["code"] for item in result["findings"]}
    assert "pvt_point_coordinates_mismatch" in codes
    assert "pvt_point_model_section_mismatch" in codes


def test_raw_evidence_rejects_empty_output_and_invalid_producer(tmp_path: Path) -> None:
    model_root, payload_root, deck_root, tools = _setup(tmp_path)
    plan = build_physical_pvt_plan(
        _contract(), registry=_registry(), tools=tools, model_root=model_root,
        payload_root=payload_root, deck_root=deck_root, cases=_cases()
    )
    observations = []
    for point in plan.points:
        point.output_path.mkdir(parents=True)
        observations.append(
            {
                "id": point.point_id,
                "coordinates": dict(point.coordinates),
                "model_section": point.model_section,
                "status": "PASS",
                "raw_output": point.output_path.relative_to(payload_root).as_posix(),
                "metrics": {},
            }
        )
    result = evaluate_physical_pvt_results(
        _contract(), observations, payload_root=payload_root,
        producer="spectre_pvt_test_v1@contract-v1", cases=_cases()
    )
    assert result["status"] == "BLOCKED_EVIDENCE"
    assert any(item["code"] == "pvt_raw_output_empty" for item in result["findings"])
    invalid = evaluate_physical_pvt_results(
        _contract(), observations, payload_root=payload_root,
        producer="spectre;rm", cases=_cases()
    )
    assert invalid["code"] == "pvt_producer_invalid"
