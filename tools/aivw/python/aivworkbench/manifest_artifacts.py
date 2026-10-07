"""Index and validate explicitly declared manifest files."""

from __future__ import annotations
from pathlib import Path
from typing import Iterable, Mapping
from .artifact_paths import PathContractError, path_has_symlink_component, trusted_root, validate_relative_path
from .errors import WorkspaceError
from .workspace import sha256_file



def artifact_index(root: Path, paths: Iterable[Path]) -> list[dict[str, object]]:
    """Hash an explicit artifact set without recursively hashing large EDA trees."""
    try:
        physical_root = trusted_root(root)
    except PathContractError as exc:
        raise WorkspaceError(str(exc)) from exc
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for candidate in sorted((Path(item) for item in paths), key=lambda item: str(item)):
        path = candidate if candidate.is_absolute() else root / candidate
        if path_has_symlink_component(physical_root, path) or not path.is_file():
            raise WorkspaceError(f"manifest artifact must be a regular file: {path}")
        physical = path.resolve()
        if not physical.is_relative_to(physical_root):
            raise WorkspaceError(f"manifest artifact escaped its run root: {path}")
        relative = physical.relative_to(physical_root).as_posix()
        if relative in seen:
            raise WorkspaceError(f"duplicate manifest artifact: {relative}")
        seen.add(relative)
        records.append(
            {
                "path": relative,
                "sha256": sha256_file(physical),
                "size": physical.stat().st_size,
            }
        )
    return records


def verify_indexed_files(root: Path, records: object, role: str) -> None:
    assert isinstance(records, list)
    try:
        physical_root = trusted_root(root)
    except PathContractError as exc:
        raise WorkspaceError(str(exc)) from exc
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise WorkspaceError(f"invalid {role} artifact record")
        relative_text = record.get("path")
        try:
            relative_text = validate_relative_path(relative_text, f"{role} artifact")
        except PathContractError as exc:
            raise WorkspaceError(str(exc)) from exc
        if relative_text in seen:
            raise WorkspaceError(f"unsafe or duplicate {role} artifact path: {relative_text}")
        seen.add(relative_text)
        path = physical_root / relative_text
        if path_has_symlink_component(physical_root, path) or not path.is_file() or not path.resolve().is_relative_to(physical_root):
            raise WorkspaceError(f"missing or unsafe {role} artifact: {relative_text}")
        if record.get("size") != path.stat().st_size or record.get("sha256") != sha256_file(path):
            raise WorkspaceError(f"{role} artifact hash mismatch: {relative_text}")


def safe_relative_path(value: str, label: str) -> str:
    try:
        return validate_relative_path(value, label)
    except PathContractError as exc:
        raise WorkspaceError(str(exc)) from exc

