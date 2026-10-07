"""Validate declared qualification artifact and manifest provenance."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .errors import WorkspaceError
from .ldo_qualification_paths import qualification_safe_child, qualification_safe_root
from .ldo_qualification_values import qualification_valid_digest
from .manifest import verify_split_manifests
from .workspace import sha256_file


def qualification_validate_artifacts(records: object, *, artifact_root: str | Path | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    normalized: list[dict[str, Any]] = []
    findings: list[str] = []
    if not isinstance(records, list) or not records:
        return [], ["evidence artifact index is missing or empty"]
    root: Path | None = None
    if artifact_root is not None:
        try:
            root = qualification_safe_root(artifact_root)
        except ValueError as exc:
            findings.append(str(exc))
    seen: set[str] = set()
    for index, raw in enumerate(records):
        if not isinstance(raw, Mapping):
            findings.append("artifact[%d] is not an object" % index)
            continue
        path_value = raw.get("path")
        digest = raw.get("sha256")
        size = raw.get("size")
        if not isinstance(path_value, str) or not path_value or Path(path_value).is_absolute() or ".." in Path(path_value).parts or "\\" in path_value:
            findings.append("artifact[%d] path is unsafe" % index)
            continue
        if path_value in seen:
            findings.append("duplicate artifact path: %s" % path_value)
        seen.add(path_value)
        if not qualification_valid_digest(digest):
            findings.append("artifact %s has an invalid sha256" % path_value)
            continue
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            findings.append("artifact %s has an invalid size" % path_value)
            continue
        record = {"path": path_value, "sha256": digest, "size": size}
        if root is not None:
            try:
                path = qualification_safe_child(root, path_value, kind="file")
                actual_size = path.stat().st_size
                actual_hash = sha256_file(path)
                if actual_size != size or actual_hash != digest:
                    findings.append("artifact hash/size mismatch: %s" % path_value)
            except ValueError as exc:
                findings.append(str(exc))
        normalized.append(record)
    return normalized, sorted(set(findings))


def qualification_validate_manifest_pair(value: object) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(value, Mapping):
        return {}, ["manifest_pair is missing"]
    result = dict(value)
    findings: list[str] = []
    if result.get("status") != "PASS":
        findings.append("manifest pair status is not PASS")
    if result.get("artifacts_verified") is not True:
        findings.append("manifest pair artifacts_verified is not true")
    if result.get("run_status") not in {"PASS", "QUALIFIED"}:
        findings.append("manifest pair run_status is not PASS")
    control = result.get("control_manifest")
    payload = result.get("payload_manifest")
    if isinstance(control, str) and isinstance(payload, str) and Path(control).is_file() and Path(payload).is_file():
        try:
            checked = verify_split_manifests(Path(control), Path(payload))
            result["verified_again"] = checked
        except (WorkspaceError, OSError, ValueError) as exc:
            findings.append("split manifest verification failed: %s" % exc)
    return result, sorted(set(findings))
