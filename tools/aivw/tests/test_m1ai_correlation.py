from __future__ import annotations

import json
from pathlib import Path

import pytest

from aivworkbench.errors import EnvironmentError
from aivworkbench.m1ai_correlation import (
    evaluate_correlation,
    load_correlation_policy,
    run_m1_ai_correlation,
)
from aivworkbench.profiles import Profile
from aivworkbench.workspace import resolve_launch_paths
from aivworkbench.workspace import sha256_file


def _manifest(path: Path, run_id: str, kind: str, artifact: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run_id,
                "kind": kind,
                "status": "PASS",
                "artifacts": [
                    {
                        "path": artifact.relative_to(path.parent).as_posix(),
                        "sha256": sha256_file(artifact),
                        "size": artifact.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _evidence(tmp_path: Path, role: str, cases: list[dict[str, object]] | None = None) -> Path:
    run_id = f"{role}-run"
    source_root = tmp_path / f"{role}-source"
    source_root.mkdir(exist_ok=True)
    manifest = source_root / "manifest.json"
    artifact = source_root / f"{role}-model.txt"
    artifact.write_text(f"{role} model\n", encoding="utf-8")
    _manifest(
        manifest,
        run_id,
        "m1-ai-golden-nominal" if role == "spectre" else "m1-ai-rnm-generation-check-tb",
        artifact,
    )
    cases = cases or [
        {
            "id": "nominal-positive",
            "type": "positive_differential",
            "corner": {"VDD": "1.2", "temperature": "27", "process": "tt"},
            "measurements": {"decision": "positive", "out": 1.2, "outb": 0.0},
        },
        {
            "id": "nominal-negative",
            "type": "negative_differential",
            "corner": {"VDD": "1.2", "temperature": "27", "process": "tt"},
            "measurements": {"decision": "negative", "out": 0.0, "outb": 1.2},
        },
        {
            "id": "nominal-tie",
            "type": "zero_differential",
            "corner": {"VDD": "1.2", "temperature": "27", "process": "tt"},
            "measurements": {"decision": "tie", "out": 0.6, "outb": 0.6},
        },
    ]
    producer_root = tmp_path / f"{role}-producer"
    evidence_dir = producer_root / "evidence"
    evidence_dir.mkdir(parents=True)
    path = evidence_dir / f"{role}-evidence.json"
    stimulus_cases = {
        str(case["id"]): {
            "type": str(case["type"]),
            "Din+": 0.7
            if case["type"] == "positive_differential"
            else 0.5
            if case["type"] == "negative_differential"
            else 0.6,
            "Din-": 0.5
            if case["type"] == "positive_differential"
            else 0.7
            if case["type"] == "negative_differential"
            else 0.6,
        }
        for case in cases
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": f"{role}-correlation-evidence",
                "target": {"library": "saradc", "cell": "comparator_new", "module": "comparator_new"},
                "source_run": {
                    "manifest": str(manifest),
                    "manifest_sha256": sha256_file(manifest),
                    "run_id": run_id,
                    "artifact": {
                        "path": artifact.name,
                        "sha256": sha256_file(artifact),
                    },
                },
                "stimulus": {
                    "corner": {"VDD": "1.2", "temperature": "27", "process": "tt"},
                    "clock": {"edge": "rising", "sample_delay_seconds": 1.0e-9},
                    "cases": stimulus_cases,
                },
                "cases": cases,
            }
        ),
        encoding="utf-8",
    )
    producer_manifest = producer_root / "manifest.json"
    producer_manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": f"{role}-producer-run",
                "kind": f"m1-ai-{role}-correlation-input",
                "status": "PASS",
                "artifacts": [
                    {
                        "path": path.relative_to(producer_root).as_posix(),
                        "sha256": sha256_file(path),
                        "size": path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _profile(tmp_path: Path) -> Profile:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text("{}", encoding="utf-8")
    return Profile(
        name="test",
        display_name="Test",
        workspace_root=tmp_path,
        setup_script=tmp_path / "setup.bashrc",
        modules=(),
        tools=(),
        inputs=(),
        projects={},
        pilots={},
        source_path=profile_path,
    )


def test_correlation_policy_and_matching_evidence_pass(tmp_path: Path) -> None:
    policy = load_correlation_policy()
    spectre = json.loads(_evidence(tmp_path, "spectre").read_text())
    rnm = json.loads(_evidence(tmp_path, "rnm").read_text())
    result = evaluate_correlation(spectre, rnm, policy)
    assert result["status"] == "PASS"
    assert result["case_count"] == 3
    assert result["metric_failure_count"] == 0


def test_correlation_rejects_corner_mismatch(tmp_path: Path) -> None:
    policy = load_correlation_policy()
    spectre = json.loads(_evidence(tmp_path, "spectre").read_text())
    rnm = json.loads(_evidence(tmp_path, "rnm").read_text())
    for case in rnm["cases"]:
        case["corner"]["VDD"] = "1.15"
    rnm["stimulus"]["corner"]["VDD"] = "1.15"
    with pytest.raises(EnvironmentError, match="stimulus vectors"):
        evaluate_correlation(spectre, rnm, policy)


def test_correlation_rejects_stimulus_or_sampling_mismatch(tmp_path: Path) -> None:
    policy = load_correlation_policy()
    spectre = json.loads(_evidence(tmp_path, "spectre").read_text())
    rnm = json.loads(_evidence(tmp_path, "rnm").read_text())
    rnm["stimulus"]["cases"]["nominal-positive"]["Din+"] = 0.71
    with pytest.raises(EnvironmentError, match="stimulus vectors"):
        evaluate_correlation(spectre, rnm, policy)


def test_correlation_reports_metric_mismatch_without_promoting_model(tmp_path: Path) -> None:
    policy = load_correlation_policy()
    spectre = json.loads(_evidence(tmp_path, "spectre").read_text())
    rnm = json.loads(_evidence(tmp_path, "rnm").read_text())
    rnm["cases"][0]["measurements"]["out"] = 1.05
    result = evaluate_correlation(spectre, rnm, policy)
    assert result["status"] == "FAIL_CORRELATION"
    positive = next(case for case in result["cases"] if case["id"] == "nominal-positive")
    assert positive["metrics"]["out"]["passed"] is False


def test_correlation_runner_without_evidence_is_blocked_and_isolated(tmp_path: Path) -> None:
    result = run_m1_ai_correlation(
        _profile(tmp_path), resolve_launch_paths(tmp_path)
    )
    assert result["status"] == "BLOCKED_INPUT"
    run_dir = Path(str(result["run_dir"]))
    assert run_dir.is_relative_to(tmp_path / ".aivw")
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["provenance"]["supplement_promotion"] == "not performed"


def test_correlation_runner_publishes_input_hashes_and_result(tmp_path: Path) -> None:
    spectre = _evidence(tmp_path, "spectre")
    rnm = _evidence(tmp_path, "rnm")
    result = run_m1_ai_correlation(
        _profile(tmp_path),
        resolve_launch_paths(tmp_path),
        spectre_evidence=spectre,
        rnm_evidence=rnm,
    )
    assert result["status"] == "PASS"
    run_dir = Path(str(result["run_dir"]))
    assert (run_dir / "inputs/spectre-evidence.json").is_file()
    assert (run_dir / "inputs/rnm-evidence.json").is_file()
    assert (run_dir / "correlation-result.json").is_file()
    assert any(item["path"] == "correlation-result.json" for item in result["artifacts"])


def test_correlation_rejects_non_finite_numeric_measurements(tmp_path: Path) -> None:
    policy = load_correlation_policy()
    spectre = json.loads(_evidence(tmp_path, "spectre").read_text())
    rnm = json.loads(_evidence(tmp_path, "rnm").read_text())
    rnm["cases"][0]["measurements"]["out"] = float("nan")
    with pytest.raises(EnvironmentError, match="must be numeric"):
        evaluate_correlation(spectre, rnm, policy)


def test_correlation_rejects_excluded_workspace_in_evidence(tmp_path: Path) -> None:
    policy = load_correlation_policy()
    spectre = json.loads(_evidence(tmp_path, "spectre").read_text())
    rnm = json.loads(_evidence(tmp_path, "rnm").read_text())
    rnm["source_run"]["manifest"] = "/workarea/smic28/manifest.json"
    with pytest.raises(EnvironmentError, match="smic28"):
        evaluate_correlation(spectre, rnm, policy)


def test_correlation_rejects_source_artifact_tampering(tmp_path: Path) -> None:
    policy = load_correlation_policy()
    spectre = json.loads(_evidence(tmp_path, "spectre").read_text())
    rnm_path = _evidence(tmp_path, "rnm")
    rnm = json.loads(rnm_path.read_text())
    artifact = tmp_path / "rnm-source" / "rnm-model.txt"
    artifact.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(EnvironmentError, match="artifact hash"):
        evaluate_correlation(spectre, rnm, policy)


def test_correlation_runner_rejects_evidence_not_bound_by_producer(tmp_path: Path) -> None:
    spectre = _evidence(tmp_path, "spectre")
    rnm = _evidence(tmp_path, "rnm")
    rnm.write_text(rnm.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    result = run_m1_ai_correlation(
        _profile(tmp_path),
        resolve_launch_paths(tmp_path),
        spectre_evidence=spectre,
        rnm_evidence=rnm,
    )
    assert result["status"] == "BLOCKED_INPUT"
    assert "producer manifest" in str(result["error"])
