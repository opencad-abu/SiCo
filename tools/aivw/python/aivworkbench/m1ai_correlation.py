from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import EnvironmentError
from .profiles import Profile, product_root
from .workspace import LaunchPaths, allocate_run, sha256_file, stable_digest, write_json_once
from .m1ai_correlation_sources import _read_json, _validate_evidence_producer
from .m1ai_correlation_policy import load_correlation_policy
from .m1ai_correlation_evaluator import evaluate_correlation
from .m1ai_correlation_paths import _reject_excluded_path, _reject_excluded_payload  # noqa: F401
from .m1ai_correlation_sources import _validate_source_run  # noqa: F401

POLICY_NAME = "comparator_new_correlation.json"

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _artifact_index(root: Path) -> list[dict[str, object]]:
    return [{"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path), "size": path.stat().st_size} for path in sorted(root.rglob("*")) if path.is_file()]

def run_m1_ai_correlation(
    profile: Profile,
    launch: LaunchPaths,
    *,
    timeout: float = 300.0,
    spectre_evidence: str | Path | None = None,
    rnm_evidence: str | Path | None = None,
    policy_path: str | Path | None = None,
) -> dict[str, object]:
    """Run the offline evidence gate; no simulation or source OA mutation occurs."""
    del profile, timeout  # The offline gate has no tool process to time out.
    started = _now()
    policy_file = Path(policy_path).expanduser() if policy_path else product_root() / "contracts" / POLICY_NAME
    request = {
        "pilot": "m1-ai",
        "phase": "spectre-to-rnm-correlation",
        "policy_sha256": sha256_file(policy_file) if policy_file.is_file() else "",
        "spectre_evidence": str(spectre_evidence or ""),
        "rnm_evidence": str(rnm_evidence or ""),
        "logical_cwd": str(launch.logical_cwd),
    }
    digest_inputs = {
        **request,
        "spectre_evidence_sha256": sha256_file(Path(spectre_evidence))
        if spectre_evidence and Path(spectre_evidence).is_file()
        else "",
        "rnm_evidence_sha256": sha256_file(Path(rnm_evidence))
        if rnm_evidence and Path(rnm_evidence).is_file()
        else "",
    }
    run = allocate_run(launch, "m1-ai-correlation", stable_digest(digest_inputs))
    inputs_dir = run.root / "inputs"
    inputs_dir.mkdir()
    status = "BLOCKED_INPUT"
    error = ""
    policy: dict[str, Any] | None = None
    result: dict[str, Any] = {}
    source_records: dict[str, Any] = {}
    try:
        if not spectre_evidence or not rnm_evidence:
            raise EnvironmentError(
                "both --spectre-evidence and --rnm-evidence are required for correlation"
            )
        policy = load_correlation_policy(policy_file)
        spectre_path, rnm_path = Path(spectre_evidence), Path(rnm_evidence)
        producer_records = {
            "spectre": _validate_evidence_producer(spectre_path, role="spectre"),
            "rnm": _validate_evidence_producer(rnm_path, role="rnm"),
        }
        spectre_payload = _read_json(spectre_path, label="Spectre evidence")
        rnm_payload = _read_json(rnm_path, label="RNM evidence")
        write_json_once(inputs_dir / "spectre-evidence.json", spectre_payload)
        write_json_once(inputs_dir / "rnm-evidence.json", rnm_payload)
        result = evaluate_correlation(spectre_payload, rnm_payload, policy)
        source_records = {
            "spectre_evidence": {
                "path": str(spectre_path),
                "sha256": sha256_file(spectre_path),
                "producer": producer_records["spectre"],
            },
            "rnm_evidence": {
                "path": str(rnm_path),
                "sha256": sha256_file(rnm_path),
                "producer": producer_records["rnm"],
            },
        }
        status = str(result["status"])
    except EnvironmentError as exc:
        error = str(exc)

    if policy is not None:
        write_json_once(run.root / "correlation-result.json", {
            "schema_version": 1,
            "policy": {"path": str(policy_file), "sha256": sha256_file(policy_file)},
            "result": result,
        })
    manifest = {
        "schema_version": 1,
        "product": "AI Verification Workbench",
        "kind": "m1-ai-spectre-to-rnm-correlation",
        "status": status,
        "started_at": started,
        "finished_at": _now(),
        "run_id": run.run_id,
        "run_dir": str(run.root),
        "request": request,
        "policy": {
            "path": str(policy_file),
            "sha256": request["policy_sha256"],
            "status": policy.get("status") if policy else "unavailable",
        },
        "inputs": source_records,
        "correlation": result,
        "error": error,
        "provenance": {
            "generated_at": _now(),
            "human_review_required": True,
            "supplement_promotion": "not performed",
        },
        "does_not_prove": [
            "RNM/SPICE waveform equivalence outside the supplied cases and metrics",
            "propagation delay or metastability behavior unless explicitly measured",
            "mismatch Monte Carlo or ADC system-level performance",
        ],
        "next_gate": (
            "human review and calibrated supplement update"
            if status == "PASS"
            else "human review of correlation mismatches"
            if status == "FAIL_CORRELATION"
            else "collect paired Spectre/RNM evidence"
        ),
    }
    manifest["artifacts"] = _artifact_index(run.root)
    write_json_once(run.manifest, manifest)
    return manifest


__all__=["evaluate_correlation","load_correlation_policy","run_m1_ai_correlation"]
