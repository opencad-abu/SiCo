"""Validate the freshness and completeness of comparator simulation outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..artifact_paths import path_has_symlink_component
from .xcelium_evidence_contract import ADAPTER_NAME
from .xcelium_evidence_routes import qualified_model_parameter_routes


def preflight_outputs(
    payload_root: Path, plan: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Reject bound evidence paths that belong to an earlier attempt.

        A split run normally receives a fresh payload directory, but callers
        can still accidentally resume a run or point an adapter at a reused
        payload.  Xcelium's coverage and SHM writers are not a provenance
        boundary: an existing directory could contain a valid-looking result
        from a previous invocation.  Require each adapter-owned output path to
        be absent before simulator startup so only this invocation can create
        the evidence later verified by :meth:`verify_outputs`.
        """
    if not isinstance(plan, Mapping):
        return [
            {
                "code": "xcelium_evidence_output_stale",
                "type": "plan",
                "path": ".",
                "reason": "adapter plan must be an object",
            }
        ]
    findings: list[dict[str, Any]] = []
    bound_paths = (
        ("coverage", "evidence/coverage")
        if isinstance(plan.get("coverage"), Mapping)
        else None,
        ("waveform", "evidence/waves.shm")
        if isinstance(plan.get("waveform"), Mapping)
        else None,
    )
    for item in bound_paths:
        if item is None:
            continue
        kind, relative = item
        candidate = payload_root / Path(relative)
        if (
            candidate.exists()
            or candidate.is_symlink()
            or path_has_symlink_component(payload_root, candidate)
        ):
            findings.append(
                {
                    "code": "xcelium_evidence_output_stale",
                    "type": kind,
                    "path": relative,
                    "reason": "bound evidence path already exists before Xcelium invocation",
                }
            )
    return findings


def verify_outputs(
    payload_root: Path,
    plan: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *, adapter_name: str = ADAPTER_NAME,
) -> list[dict[str, Any]]:
    """Confirm that runtime paths contain the artifacts this adapter owns."""
    if not isinstance(plan, Mapping):
        return [
            {
                "code": "xcelium_evidence_matrix_invalid",
                "detail": "plan must be an object",
                "adapter": adapter_name,
            }
        ]
    if not isinstance(evidence, Mapping):
        return [
            {
                "code": "xcelium_evidence_runtime_invalid",
                "detail": "evidence must be an object",
                "adapter": adapter_name,
            }
        ]
    findings = verify_model_parameter_routes(plan, evidence, adapter_name=adapter_name)
    coverage = plan.get("coverage")
    if isinstance(coverage, Mapping):
        root = payload_root / "evidence" / "coverage"
        if path_has_symlink_component(payload_root, root):
            findings.append(
                {
                    "code": "xcelium_coverage_output_symlink",
                    "path": "evidence/coverage",
                }
            )
        elif not root.is_dir():
            findings.append(
                {
                    "code": "xcelium_coverage_output_missing",
                    "path": "evidence/coverage",
                }
            )
        else:
            raw_suffixes = {
                suffix
                for suffix in (".ucd", ".ucm")
                for path in root.glob(f"**/*{suffix}")
                if path.is_file()
                and not path.is_symlink()
                and not path_has_symlink_component(root, path)
            }
            if raw_suffixes != {".ucd", ".ucm"}:
                findings.append(
                    {
                        "code": "xcelium_coverage_raw_output_missing",
                        "path": "evidence/coverage",
                        "expected": [".ucd", ".ucm"],
                        "actual": sorted(raw_suffixes),
                    }
                )
    waveform = plan.get("waveform")
    if isinstance(waveform, Mapping):
        path = payload_root / "evidence" / "waves.shm"
        retention = waveform.get("retention")
        exists = path.exists() or path.is_symlink()
        symlinked = path_has_symlink_component(payload_root, path)
        # Check the bound path even when the final directory has not been
        # created.  A symlinked parent must fail closed rather than being
        # reported merely as a missing optional output.
        if symlinked:
            findings.append(
                {
                    "code": "xcelium_waveform_output_symlink",
                    "path": "evidence/waves.shm",
                }
            )
        elif exists and not path.is_dir():
            findings.append(
                {
                    "code": "xcelium_waveform_output_invalid",
                    "path": "evidence/waves.shm",
                }
            )
        elif exists:
            has_dsn = any(
                item.is_file()
                and not item.is_symlink()
                and not path_has_symlink_component(path, item)
                for item in path.glob("*.dsn")
            )
            has_trn = any(
                item.is_file()
                and not item.is_symlink()
                and not path_has_symlink_component(path, item)
                for item in path.glob("*.trn")
            )
            if not has_dsn or not has_trn:
                findings.append(
                    {
                        "code": "xcelium_waveform_output_missing",
                        "path": "evidence/waves.shm",
                        "expected": ["*.dsn", "*.trn"],
                    }
                )
        if retention == "never" and exists:
            findings.append(
                {"code": "waveform_artifact_present_when_forbidden", "path": "evidence/waves.shm"}
            )
        if retention == "on_failure" and evidence.get("status") == "PASS" and exists:
            findings.append(
                {"code": "waveform_artifact_retained_on_clean_pass", "path": "evidence/waves.shm"}
            )
        if retention == "always" and not exists:
            findings.append(
                {"code": "xcelium_waveform_output_missing", "path": "evidence/waves.shm"}
            )
    return findings


def verify_model_parameter_routes(
    plan: Mapping[str, Any], evidence: Mapping[str, Any],
    *, adapter_name: str = ADAPTER_NAME,
) -> list[dict[str, Any]]:
    """Fail closed on missing or forged comparator instance routing."""
    findings: list[dict[str, Any]] = []
    try:
        routes = qualified_model_parameter_routes(plan)
    except (TypeError, ValueError, AttributeError) as exc:
        return [
            {
                "code": "xcelium_evidence_matrix_invalid",
                "detail": str(exc),
                "adapter": adapter_name,
            }
        ]
    events = evidence.get("events")
    if not isinstance(events, list):
        events = []
    case_events: dict[str, list[Mapping[str, Any]]] = {}
    summaries: list[Mapping[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if event.get("event") == "case" and isinstance(event.get("id"), str):
            case_events.setdefault(str(event["id"]), []).append(event)
        elif event.get("event") == "summary":
            summaries.append(event)

    route_field = "model_parameter_route"
    summary_field = "model_parameter_instance_count"
    if routes is None:
        for case_id, occurrences in sorted(case_events.items()):
            if any(route_field in event for event in occurrences):
                findings.append(
                    {
                        "code": "xcelium_matrix_route_unexpected",
                        "case_id": case_id,
                    }
                )
        if any(summary_field in summary for summary in summaries):
            findings.append(
                {"code": "xcelium_matrix_instance_count_unexpected"}
            )
        return findings

    expected_routes = routes["case_routes"]
    for case_id, expected in sorted(expected_routes.items()):
        occurrences = case_events.get(case_id, [])
        if len(occurrences) != 1:
            findings.append(
                {
                    "code": "xcelium_matrix_route_event_count_mismatch",
                    "case_id": case_id,
                    "expected": 1,
                    "actual": len(occurrences),
                }
            )
            continue
        actual = occurrences[0].get(route_field)
        if actual != expected:
            findings.append(
                {
                    "code": "xcelium_matrix_route_mismatch",
                    "case_id": case_id,
                    "expected": expected,
                    "actual": actual,
                }
            )
    for case_id, occurrences in sorted(case_events.items()):
        if case_id in expected_routes:
            continue
        if any(route_field in event for event in occurrences):
            findings.append(
                {
                    "code": "xcelium_matrix_route_unexpected",
                    "case_id": case_id,
                }
            )
    if len(summaries) != 1:
        findings.append(
            {
                "code": "xcelium_matrix_route_summary_count_mismatch",
                "expected": 1,
                "actual": len(summaries),
            }
        )
    elif summaries[0].get(summary_field) != routes["instance_count"]:
        findings.append(
            {
                "code": "xcelium_matrix_instance_count_mismatch",
                "expected": routes["instance_count"],
                "actual": summaries[0].get(summary_field),
            }
        )
    return findings
