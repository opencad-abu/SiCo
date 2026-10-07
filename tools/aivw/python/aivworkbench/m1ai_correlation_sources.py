from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .errors import EnvironmentError, WorkspaceError
from .manifest import resolve_indexed_artifact
from .workspace import sha256_file
from .m1ai_correlation_paths import _reject_excluded_path, _reject_excluded_payload

_EXPECTED_KINDS = {"spectre": "spectre-correlation-evidence", "rnm": "rnm-correlation-evidence"}
_EXPECTED_PRODUCER_KINDS = {"spectre": "m1-ai-spectre-correlation-input", "rnm": "m1-ai-rnm-correlation-input"}

def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_absolute():
        raise EnvironmentError(f"{label} must be an absolute path: {path}")
    _reject_excluded_path(path, label=label)
    if not path.is_file() or path.stat().st_size == 0:
        raise EnvironmentError(f"{label} is missing or empty: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvironmentError(f"{label} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise EnvironmentError(f"{label} root must be an object: {path}")
    _reject_excluded_payload(payload, label=label)
    return payload
def _validate_evidence_producer(path: Path, *, role: str) -> dict[str, Any]:
    """Bind an evidence JSON to the PASS exporter manifest that published it."""
    producer_manifest = path.parent.parent / "manifest.json"
    payload = _read_json(producer_manifest, label=f"{role} evidence producer manifest")
    if payload.get("status") != "PASS" or payload.get("kind") != _EXPECTED_PRODUCER_KINDS[role]:
        raise EnvironmentError(f"{role} evidence producer run is not an approved PASS")
    root = producer_manifest.parent.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise EnvironmentError(f"{role} evidence is outside its producer run")
    relative = resolved.relative_to(root).as_posix()
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise EnvironmentError(f"{role} evidence producer has no artifact index")
    evidence_hash = sha256_file(path)
    record = next(
        (
            item
            for item in artifacts
            if isinstance(item, Mapping) and str(item.get("path", "")) == relative
        ),
        None,
    )
    if not isinstance(record, Mapping) or str(record.get("sha256", "")) != evidence_hash:
        raise EnvironmentError(f"{role} evidence is not bound by its producer manifest")
    return {
        "manifest": str(producer_manifest),
        "manifest_sha256": sha256_file(producer_manifest),
        "run_id": str(payload.get("run_id", "")),
        "kind": str(payload.get("kind", "")),
        "artifact": {"path": relative, "sha256": evidence_hash},
    }
def _validate_source_run(value: object, *, role: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EnvironmentError(f"{role} evidence requires source_run provenance")
    manifest_value = value.get("manifest")
    run_id = str(value.get("run_id", ""))
    manifest = Path(str(manifest_value)) if manifest_value else Path()
    _reject_excluded_path(manifest, label=f"{role} source_run manifest")
    if not manifest.is_absolute() or not manifest.is_file():
        raise EnvironmentError(f"{role} source_run manifest is missing: {manifest}")
    payload = _read_json(manifest, label=f"{role} source manifest")
    if str(payload.get("run_id", "")) != run_id:
        raise EnvironmentError(f"{role} source_run run_id does not match manifest")
    if payload.get("status") != "PASS":
        raise EnvironmentError(f"{role} source run is not PASS: {run_id}")
    declared_hash = value.get("manifest_sha256")
    if not isinstance(declared_hash, str) or declared_hash != sha256_file(manifest):
        raise EnvironmentError(f"{role} source_run manifest hash is missing or does not match")
    artifact = value.get("artifact")
    if not isinstance(artifact, Mapping):
        raise EnvironmentError(f"{role} source_run artifact provenance is required")
    artifact_name = artifact.get("path")
    artifact_hash = artifact.get("sha256")
    if not isinstance(artifact_name, str) or not artifact_name or Path(artifact_name).is_absolute():
        raise EnvironmentError(f"{role} source_run artifact path must be relative")
    artifact_rel = Path(artifact_name)
    if ".." in artifact_rel.parts:
        raise EnvironmentError(f"{role} source_run artifact path escapes the manifest directory")
    if payload.get("kind") == "aivw_control_manifest" and payload.get("role") == "control":
        try:
            resolved = resolve_indexed_artifact(manifest, artifact_rel.as_posix())
        except WorkspaceError as exc:
            raise EnvironmentError(f"{role} split source artifact is invalid: {exc}") from exc
        if not isinstance(artifact_hash, str) or artifact_hash != resolved.sha256:
            raise EnvironmentError(f"{role} source_run artifact hash is missing or does not match")
        if payload.get("status") != "PASS":
            raise EnvironmentError(f"{role} source run is not PASS: {run_id}")
        if role == "rnm":
            allowed = {"m1-ai-rnm-generation-check-tb", "m1-ai-rnm-correlation-input"}
            if resolved.producer_kind not in allowed:
                raise EnvironmentError(
                    f"unexpected RNM source run kind: {resolved.producer_kind}"
                )
        elif resolved.producer_kind not in {"m1-ai-golden-nominal", "m1-ai-golden-pvt"}:
            raise EnvironmentError(
                f"unexpected Spectre source run kind: {resolved.producer_kind}"
            )
        return {
            "manifest": str(manifest),
            "manifest_sha256": sha256_file(manifest),
            "artifact_manifest": str(resolved.artifact_manifest),
            "run_id": run_id,
            "kind": resolved.producer_kind,
            "status": "PASS",
            "expected_evidence_kind": _EXPECTED_KINDS[role],
            "artifact": {
                "path": resolved.relative_path,
                "sha256": resolved.sha256,
                "storage_role": resolved.storage_role,
            },
        }
    artifact_path = (manifest.parent / artifact_rel).resolve(strict=False)
    manifest_root = manifest.parent.resolve()
    if not artifact_path.is_relative_to(manifest_root) or not artifact_path.is_file():
        raise EnvironmentError(f"{role} source_run artifact is missing: {artifact_name}")
    actual_artifact_hash = sha256_file(artifact_path)
    if not isinstance(artifact_hash, str) or artifact_hash != actual_artifact_hash:
        raise EnvironmentError(f"{role} source_run artifact hash is missing or does not match")
    manifest_artifacts = payload.get("artifacts")
    if not isinstance(manifest_artifacts, list):
        raise EnvironmentError(f"{role} source manifest does not contain an artifact index")
    indexed = next(
        (
            item
            for item in manifest_artifacts
            if isinstance(item, Mapping) and str(item.get("path", "")) == artifact_rel.as_posix()
        ),
        None,
    )
    if not isinstance(indexed, Mapping) or str(indexed.get("sha256", "")) != actual_artifact_hash:
        raise EnvironmentError(f"{role} source artifact is not bound by the manifest index")
    expected_kind = _EXPECTED_KINDS[role]
    if role == "rnm":
        allowed = {"m1-ai-rnm-generation-check-tb", "m1-ai-rnm-correlation-input"}
        if payload.get("kind") not in allowed:
            raise EnvironmentError(f"unexpected RNM source run kind: {payload.get('kind')}")
    elif payload.get("kind") not in {"m1-ai-golden-nominal", "m1-ai-golden-pvt"}:
        raise EnvironmentError(f"unexpected Spectre source run kind: {payload.get('kind')}")
    return {
        "manifest": str(manifest),
        "manifest_sha256": sha256_file(manifest),
        "run_id": run_id,
        "kind": str(payload.get("kind")),
        "status": "PASS",
        "expected_evidence_kind": expected_kind,
        "artifact": {
            "path": artifact_rel.as_posix(),
            "sha256": actual_artifact_hash,
        },
    }

__all__=["_read_json","_validate_evidence_producer","_validate_source_run"]
