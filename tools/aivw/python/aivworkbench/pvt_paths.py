"""Filesystem contracts for physical PVT planning and evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .artifact_paths import PathContractError, path_has_symlink_component, validate_relative_path

def approved_model_path(model_root: Path, contract: Mapping[str, Any]) -> Path | str:
    relative = Path(str(contract["model_binding"]["model_file"]))
    path = model_root / relative
    try:
        error = approved_file(model_root, path, "model file")
        return error if error is not None else path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return f"model file could not be resolved: {path} ({exc})"


def approved_directory(path: Path, label: str) -> str | None:
    try:
        if not isinstance(path, Path) or not path.is_absolute():
            return f"{label} must be an absolute path"
        if path.is_symlink() or not path.is_dir():
            return f"{label} is missing or not a regular directory: {path}"
        if path_has_symlink_component(path, path):
            return f"{label} contains a symlink component: {path}"
        return None
    except (OSError, RuntimeError) as exc:
        return f"{label} could not be inspected: {path} ({exc})"


def approved_file(root: Path, path: Path, label: str) -> str | None:
    try:
        root_error = approved_directory(root, root.name or "root")
        if root_error is not None:
            return root_error
        ensure_contained(root, path, label)
    except ValueError as exc:
        return str(exc)
    except (OSError, RuntimeError) as exc:
        return f"{label} could not be inspected: {path} ({exc})"
    try:
        if path.is_symlink() or not path.is_file():
            return f"{label} is missing or not a regular file: {path}"
        if path_has_symlink_component(root, path):
            return f"{label} contains a symlink component: {path}"
        return None
    except (OSError, RuntimeError) as exc:
        return f"{label} could not be inspected: {path} ({exc})"


def approved_executable(path: Path) -> bool:
    """Return false for missing, replaced, or non-executable tool paths."""

    if not isinstance(path, Path) or not path.is_absolute():
        return False
    try:
        return (
            not path.is_symlink()
            and path.is_file()
            and bool(path.stat().st_mode & 0o111)
        )
    except (OSError, RuntimeError):
        return False


def ensure_contained(root: Path, candidate: Path, label: str) -> None:
    if not isinstance(root, Path) or not root.is_absolute():
        raise ValueError(f"{label} root must be an absolute path")
    try:
        physical_root = root.resolve(strict=False)
        physical = candidate.resolve(strict=False)
        if not physical.is_relative_to(physical_root):
            raise ValueError(f"{label} escaped its approved root")
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, ValueError) and "escaped" in str(exc):
            raise
        raise ValueError(f"{label} could not be resolved") from exc


def path_exists_or_has_symlink(root: Path, candidate: Path) -> bool:
    try:
        return candidate.exists() or path_has_symlink_component(root, candidate)
    except (OSError, RuntimeError):
        return True


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"path escaped payload root: {path}") from exc


def diagnostic_relative_path(root: Path, path: Path) -> str:
    """Return a JSON-safe path for findings even during a filesystem race."""

    try:
        return relative_path(root, path)
    except (OSError, RuntimeError, ValueError):
        return f"<unavailable:{path}>"


def resolve_observed_path(root: Path, value: object) -> Path:
    try:
        relative = Path(validate_relative_path(value, "raw_output"))
    except PathContractError as exc:
        raise ValueError("raw_output must be a safe payload-relative path") from exc
    candidate = root / relative
    # Check the lexical path before resolving it.  Resolving first would erase
    # an in-root symlink and could make a substituted output look canonical.
    if path_has_symlink_component(root, candidate):
        raise ValueError("raw_output contains a symlink component")
    ensure_contained(root, candidate, "raw output")
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError("raw_output could not be resolved") from exc
