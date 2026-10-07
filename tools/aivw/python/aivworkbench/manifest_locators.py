"""Validate bounded file and directory locators for EDA evidence."""

from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping
from .artifact_paths import PathContractError, path_has_symlink_component, trusted_root
from .errors import WorkspaceError
from .workspace import sha256_file

from .manifest_artifacts import safe_relative_path


def artifact_locator_index(
    root: Path, locators: Iterable[Mapping[str, Any]]
) -> list[dict[str, object]]:
    """Normalize and verify external EDA file/directory locators.

    Locators are deliberately separate from ``artifacts``.  A large EDA
    database (SHM, FSDB, UCIS, or IMC) is represented by a bounded directory
    locator instead of recursively hashing every member.
    """
    try:
        physical_root = trusted_root(root)
    except PathContractError as exc:
        raise WorkspaceError(str(exc)) from exc
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in locators:
        record, _path = normalize_locator(physical_root, raw)
        relative = str(record["path"])
        if relative in seen:
            raise WorkspaceError(f"duplicate manifest artifact locator: {relative}")
        seen.add(relative)
        records.append(record)
    return sorted(records, key=lambda item: str(item["path"]))


def normalize_locator(root: Path, raw: Mapping[str, Any]) -> tuple[dict[str, object], Path]:
    if not isinstance(raw, Mapping):
        raise WorkspaceError("artifact locator must be an object")
    path_value = raw.get("path")
    relative_text = safe_relative_path(path_value, "manifest artifact locator")
    kind = raw.get("kind")
    if kind not in {"file", "directory"}:
        raise WorkspaceError(f"artifact locator kind is invalid: {relative_text}")
    producer = raw.get("producer")
    if not isinstance(producer, str) or not producer:
        raise WorkspaceError(f"artifact locator producer is invalid: {relative_text}")
    expected_exists = raw.get("exists", True)
    if expected_exists is not True:
        raise WorkspaceError(f"artifact locator must record an existing path: {relative_text}")
    candidate = root / relative_text
    if path_has_symlink_component(root, candidate):
        raise WorkspaceError(f"artifact locator is a symlink: {relative_text}")
    path = candidate.resolve()
    if not path.is_relative_to(root) or not path.exists():
        raise WorkspaceError(f"artifact locator is missing or outside root: {relative_text}")
    actual_kind = "directory" if path.is_dir() else "file" if path.is_file() else None
    if actual_kind != kind:
        raise WorkspaceError(f"artifact locator type mismatch: {relative_text}")
    normalized: dict[str, object] = {
        "path": relative_text,
        "kind": kind,
        "exists": True,
        "producer": producer,
    }
    if kind == "file":
        digest = sha256_file(path)
        size = path.stat().st_size
        raw_digest = raw.get("sha256")
        raw_size = raw.get("size")
        if (raw_digest is not None and raw_digest != digest) or (
            raw_size is not None and raw_size != size
        ):
            raise WorkspaceError(f"artifact locator hash mismatch: {relative_text}")
        normalized["sha256"] = digest
        normalized["size"] = size
    else:
        if raw.get("sha256") is not None or raw.get("size") is not None:
            raise WorkspaceError(f"directory locator has file hash fields: {relative_text}")
        normalized["sha256"] = None
    for key, value in raw.items():
        if key not in normalized and key not in {"sha256", "size"}:
            try:
                json.dumps(value, ensure_ascii=True, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise WorkspaceError(f"artifact locator field is not JSON-safe: {key}") from exc
            normalized[str(key)] = deepcopy(value)
    return normalized, path


def verify_artifact_locators(root: Path, records: object, role: str) -> None:
    if records is None:
        return
    if not isinstance(records, list):
        raise WorkspaceError(f"invalid {role} artifact locator index")
    artifact_locator_index(root, records)

