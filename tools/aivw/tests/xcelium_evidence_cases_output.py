from __future__ import annotations

import json
from pathlib import Path

import pytest

from aivworkbench.errors import WorkspaceError
from aivworkbench.executors.xcelium import (
    _evidence_locators,
)
from aivworkbench.manifest import (
    publish_split_manifests,
    resolve_indexed_locator,
    verify_split_manifests,
)
from aivworkbench.workspace import stable_digest
from xcelium_evidence_fixtures import _adapter, _event, _plan, _run


def test_output_verifier_requires_raw_ucd_and_ucm_and_rejects_parent_symlink(
    tmp_path: Path,
) -> None:
    adapter = _adapter()
    plan = _plan(retention="never")
    coverage = tmp_path / "evidence" / "coverage" / "scope"
    coverage.mkdir(parents=True)
    (coverage / "run.ucm").write_text("ucm\n", encoding="utf-8")
    findings = adapter.verify_outputs(tmp_path, plan, {"status": "PASS"})
    raw = next(
        item
        for item in findings
        if item["code"] == "xcelium_coverage_raw_output_missing"
    )
    assert raw["actual"] == [".ucm"]
    (coverage / "run.ucd").write_text("ucd\n", encoding="utf-8")
    assert adapter.verify_outputs(tmp_path, plan, {"status": "PASS"}) == []

    linked_root = tmp_path / "linked-payload"
    linked_root.mkdir()
    outside = tmp_path / "outside-evidence"
    (outside / "coverage").mkdir(parents=True)
    (linked_root / "evidence").symlink_to(outside, target_is_directory=True)
    codes = {
        item["code"]
        for item in adapter.verify_outputs(linked_root, plan, {"status": "PASS"})
    }
    assert "xcelium_coverage_output_symlink" in codes
    # The bound waveform directory is absent under the symlinked parent; the
    # path must still be rejected as a symlink rather than reported as missing.
    assert "xcelium_waveform_output_symlink" in codes


@pytest.mark.parametrize("missing_suffix", (".dsn", ".trn"))
def test_output_verifier_requires_complete_shm(
    tmp_path: Path, missing_suffix: str
) -> None:
    adapter = _adapter()
    plan = _plan()
    coverage = tmp_path / "evidence" / "coverage"
    coverage.mkdir(parents=True)
    (coverage / "run.ucm").write_text("ucm\n", encoding="utf-8")
    (coverage / "run.ucd").write_text("ucd\n", encoding="utf-8")
    waves = tmp_path / "evidence" / "waves.shm"
    waves.mkdir()
    present = ".trn" if missing_suffix == ".dsn" else ".dsn"
    (waves / f"waves{present}").write_text("waves\n", encoding="utf-8")
    codes = {
        item["code"]
        for item in adapter.verify_outputs(tmp_path, plan, {"status": "PASS"})
    }
    assert "xcelium_waveform_output_missing" in codes


def test_output_verifier_enforces_clean_pass_and_never_retention(
    tmp_path: Path,
) -> None:
    adapter = _adapter()
    coverage = tmp_path / "evidence" / "coverage"
    coverage.mkdir(parents=True)
    (coverage / "run.ucm").write_text("ucm\n", encoding="utf-8")
    (coverage / "run.ucd").write_text("ucd\n", encoding="utf-8")
    waves = tmp_path / "evidence" / "waves.shm"
    waves.mkdir()
    (waves / "waves.dsn").write_text("dsn\n", encoding="utf-8")
    (waves / "waves.trn").write_text("trn\n", encoding="utf-8")

    on_failure = _plan(retention="on_failure")
    clean_codes = {
        item["code"]
        for item in adapter.verify_outputs(tmp_path, on_failure, {"status": "PASS"})
    }
    assert "waveform_artifact_retained_on_clean_pass" in clean_codes
    failed_codes = {
        item["code"]
        for item in adapter.verify_outputs(tmp_path, on_failure, {"status": "FAIL"})
    }
    assert "waveform_artifact_retained_on_clean_pass" not in failed_codes

    never_codes = {
        item["code"]
        for item in adapter.verify_outputs(
            tmp_path, _plan(retention="never"), {"status": "PASS"}
        )
    }
    assert "waveform_artifact_present_when_forbidden" in never_codes


def test_validated_evidence_locators_publish_and_resolve_from_split_manifest(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    coverage = run.payload_root / "evidence" / "coverage"
    waves = run.payload_root / "evidence" / "waves.shm"
    coverage.mkdir(parents=True)
    waves.mkdir()
    (coverage / "run.ucm").write_text("ucm\n", encoding="utf-8")
    (coverage / "run.ucd").write_text("ucd\n", encoding="utf-8")
    (waves / "waves.dsn").write_text("dsn\n", encoding="utf-8")
    (waves / "waves.trn").write_text("trn\n", encoding="utf-8")
    plan = _plan()
    binding = _adapter().binding_for_plan(plan)
    evidence = {
        "status": "PASS",
        "waveform": plan["waveform"],
        "observed_coverage_ids": ["functional"],
        "observed_waveform_ids": ["debug-wave"],
        "events": [
            _event("coverage", "functional", "evidence/coverage"),
            _event("waveform", "debug-wave", "evidence/waves.shm"),
        ],
    }
    locators = _evidence_locators(evidence, run.payload_root, binding)
    records = tuple(
        {
            "path": locator.path.relative_to(run.payload_root).as_posix(),
            "kind": "directory",
            "exists": True,
            "producer": locator.producer,
            **dict(locator.metadata),
        }
        for locator in locators
    )
    publish_split_manifests(
        run,
        request_digest=stable_digest({"test": "xcelium-evidence"}),
        status="PASS",
        control_details={},
        payload_details={},
        payload_locators=records,
    )
    verified = verify_split_manifests(run.control_manifest, run.payload_manifest)
    assert verified["locator_counts"] == {"control": 0, "payload": 2}
    resolved = resolve_indexed_locator(run.control_manifest, "evidence/waves.shm")
    assert resolved.path == waves.resolve()
    assert resolved.producer == "xcelium.waveform"


def test_legacy_manifest_without_locator_collection_remains_compatible(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "legacy.log"
    artifact.write_text("PASS\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "legacy_run",
                "artifacts": [
                    {
                        "path": "legacy.log",
                        "sha256": stable_digest("placeholder"),
                        "size": artifact.stat().st_size,
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(WorkspaceError, match="artifact locator must have exactly one"):
        resolve_indexed_locator(manifest, "evidence/waves.shm")
