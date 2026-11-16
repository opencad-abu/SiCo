from __future__ import annotations

from pathlib import Path


from aivworkbench.pvt_adapter import (
    build_physical_pvt_plan,
    evaluate_physical_pvt_results,
)
from pvt_adapter_fixtures import (_contract, _cases, _setup, _registry, _file_registry, _fsdb_contract, _materialize_observations)


def test_fsdb_evaluator_requires_nonempty_regular_file_and_exports_file_locator(
    tmp_path: Path,
) -> None:
    model_root, payload_root, deck_root, tools = _setup(tmp_path)
    plan = build_physical_pvt_plan(
        _fsdb_contract(),
        registry=_file_registry(),
        tools=tools,
        model_root=model_root,
        payload_root=payload_root,
        deck_root=deck_root,
        cases=_cases(),
    )
    observations = []
    for point in plan.points:
        point.output_path.parent.mkdir(parents=True)
        point.output_path.write_bytes(b"fsdb evidence\n")
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
        _fsdb_contract(),
        observations,
        payload_root=payload_root,
        producer="spectre_pvt_fsdb_test_v1@contract-v1",
        cases=_cases(),
    )
    assert result["status"] == "PASS"
    assert all(item["kind"] == "file" for item in result["artifact_locators"])
    from aivworkbench.manifest import artifact_locator_index

    indexed = artifact_locator_index(payload_root, result["artifact_locators"])
    assert all(item["kind"] == "file" for item in indexed)

    first = plan.points[0]
    first.output_path.write_bytes(b"")
    empty = evaluate_physical_pvt_results(
        _fsdb_contract(),
        observations,
        payload_root=payload_root,
        producer="spectre_pvt_fsdb_test_v1@contract-v1",
        cases=_cases(),
    )
    assert any(item["code"] == "pvt_raw_output_empty" for item in empty["findings"])


def test_evaluator_rejects_output_symlink_as_unsafe(tmp_path: Path) -> None:
    model_root, payload_root, deck_root, tools = _setup(tmp_path)
    plan = build_physical_pvt_plan(
        _contract(),
        registry=_registry(),
        tools=tools,
        model_root=model_root,
        payload_root=payload_root,
        deck_root=deck_root,
        cases=_cases(),
    )
    observations = _materialize_observations(plan, payload_root)
    target = tmp_path / "outside-output"
    target.mkdir()
    unsafe = plan.points[0].output_path
    unsafe.unlink() if unsafe.is_symlink() else None
    unsafe.parent.mkdir(parents=True, exist_ok=True)
    # Replace the expected output directory with an in-root symlink.  The
    # observation path remains canonical and must still fail closed.
    for child in unsafe.iterdir():
        child.unlink()
    unsafe.rmdir()
    unsafe.symlink_to(target, target_is_directory=True)
    result = evaluate_physical_pvt_results(
        _contract(),
        observations,
        payload_root=payload_root,
        producer="spectre_pvt_test_v1@contract-v1",
        cases=_cases(),
    )
    assert any(
        item["code"] == "pvt_raw_output_missing_or_unsafe"
        for item in result["findings"]
    )


def test_evaluator_returns_json_safe_result_when_expected_output_disappears(
    tmp_path: Path,
) -> None:
    model_root, payload_root, deck_root, tools = _setup(tmp_path)
    plan = build_physical_pvt_plan(
        _contract(),
        registry=_registry(),
        tools=tools,
        model_root=model_root,
        payload_root=payload_root,
        deck_root=deck_root,
        cases=_cases(),
    )
    observations = _materialize_observations(plan, payload_root)
    vanished = plan.points[0].output_path
    for child in vanished.iterdir():
        child.unlink()
    vanished.rmdir()
    vanished.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    result = evaluate_physical_pvt_results(
        _contract(),
        observations,
        payload_root=payload_root,
        producer="spectre_pvt_test_v1@contract-v1",
        cases=_cases(),
    )
    import json

    json.dumps(result, allow_nan=False)
    assert result["status"] == "BLOCKED_EVIDENCE"
    assert any(item["code"] == "pvt_raw_output_missing_or_unsafe" for item in result["findings"])
