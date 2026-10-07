"""Persist bounded LDO live evidence and split manifests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .manifest import publish_split_manifests, verify_split_manifests
from .toolchain import safe_environment_summary
from .workspace import sha256_file, write_json_once


def finalize_live_run(
    *,
    run: Any,
    payload: Path,
    request: Mapping[str, Any],
    request_digest: str,
    profile_name: str,
    source_before: Mapping[str, Any],
    source_after: Mapping[str, Any],
    setup_modules: tuple[str, ...],
    environment: Mapping[str, str],
    tools: tuple[Any, ...],
    processes: list[Any],
    staged_cds: Path,
    staged_rule: Path,
    catalog_summary: Mapping[str, Any],
    hed_summaries: list[Mapping[str, Any]],
    runams_summaries: list[Mapping[str, Any]],
    official_bindings: list[Mapping[str, Any]],
    findings: list[str],
    error: str,
    status: str,
) -> dict[str, Any]:
    unique_findings = sorted(set(findings))
    write_json_once(payload / "source-preflight-after.json", source_after)
    details = {
        "scope": "read_only_structure",
        "source_generation": source_before.get("source_generation"),
        "source_before_status": source_before.get("status"),
        "source_after_status": source_after.get("status"),
        "catalog": dict(catalog_summary),
        "hed": list(hed_summaries),
        "runams": list(runams_summaries),
        "official_bindings": list(official_bindings),
        "findings": unique_findings,
        "error": error,
    }
    control, payload_manifest = publish_split_manifests(
        run,
        request_digest=request_digest,
        status=status,
        control_details={
            "kind": "ldo-live-structure",
            "profile": profile_name,
            "target": request["target"],
            "source_generation": source_before.get("source_generation"),
            "scope": "read_only_structure",
            "findings": unique_findings,
            "error": error,
        },
        payload_details={
            "source_generation": source_before.get("source_generation"),
            "scope": "raw_eda_evidence",
        },
        control_artifacts=(run.control_root / "request.json",),
        payload_artifacts=tuple(path for path in payload.rglob("*") if path.is_file()),
    )
    manifest_pair = verify_split_manifests(run.control_manifest, run.payload_manifest)
    return {
        "schema_version": 1,
        "kind": "ldo-live-structure",
        "status": status,
        "run_id": run.run_id,
        "run_dir": str(run.control_root),
        "payload_dir": str(run.payload_root),
        "control_manifest": str(run.control_manifest),
        "payload_manifest": str(run.payload_manifest),
        "request_digest": request_digest,
        "setup_modules": list(setup_modules),
        "environment": safe_environment_summary(environment) if environment else {},
        "tools": [item.to_dict() for item in tools],
        "processes": [item.to_dict() for item in processes],
        "details": details,
        "source_snapshot_before": dict(source_before),
        "source_snapshot_after": dict(source_after),
        "staged_cds_lib": {"sha256": sha256_file(staged_cds), "path": str(staged_cds)}
        if staged_cds.is_file()
        else None,
        "staged_connect_rule": {
            "sha256": sha256_file(staged_rule),
            "path": str(staged_rule),
        }
        if staged_rule.is_file()
        else None,
        "manifest_pair": manifest_pair,
        "control_manifest_core": control.get("binding", {}).get("self_core_sha256")
        if isinstance(control.get("binding"), Mapping)
        else None,
        "payload_manifest_core": payload_manifest.get("binding", {}).get(
            "self_core_sha256"
        )
        if isinstance(payload_manifest.get("binding"), Mapping)
        else None,
    }


__all__ = ["finalize_live_run"]
