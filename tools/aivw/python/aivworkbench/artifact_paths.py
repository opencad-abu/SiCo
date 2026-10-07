"""Explicit filesystem path contracts used by artifact producers and readers.

The helpers in this module describe filesystem facts only.  Callers keep
ownership of their domain-specific error codes and policy decisions.
"""

from __future__ import annotations

from pathlib import Path
import re


_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class PathContractError(ValueError):
    """Raised when a path cannot satisfy its declared filesystem contract."""


def validate_relative_path(value: object, label: str = "path") -> str:
    """Return a canonical slash-separated relative path.

    This is lexical validation.  It intentionally rejects aliases such as
    ``a/../b`` and repeated separators before any filesystem resolution.
    """

    if not isinstance(value, str) or not value or "\x00" in value:
        raise PathContractError(f"{label} path is invalid")
    relative = Path(value)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "\\" in value
        or _WINDOWS_DRIVE.match(value) is not None
        or any(part in {"", "."} for part in value.split("/"))
        or relative.as_posix() != value
    ):
        raise PathContractError(f"unsafe {label} path: {value}")
    return value


def path_has_symlink_component(root: str | Path, candidate: str | Path) -> bool:
    """Return true if ``candidate`` reaches a symlink from ``root``.

    Existing and missing components are both checked.  An uninspectable
    component is unsafe and therefore returns true.  A candidate outside the
    lexical root is also unsafe for this root-relative contract.
    """

    try:
        root_path = Path(root).expanduser()
        candidate_path = Path(candidate).expanduser()
        if not root_path.is_absolute():
            root_path = Path.cwd() / root_path
        if not candidate_path.is_absolute():
            candidate_path = Path.cwd() / candidate_path
        relative = candidate_path.relative_to(root_path)
    except (OSError, RuntimeError, TypeError, ValueError):
        return True
    current = Path(root_path.anchor) if root_path.anchor else Path()
    parts = root_path.parts[1:] if root_path.anchor else root_path.parts
    for part in parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    current = root_path
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def path_has_any_symlink_component(path: str | Path) -> bool:
    """Return true when an absolute or relative path contains any symlink."""

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    anchor = Path(candidate.anchor) if candidate.anchor else Path()
    current = anchor
    parts = candidate.parts[1:] if candidate.anchor else candidate.parts
    for part in parts:
        current = current / part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def trusted_root(root: str | Path) -> Path:
    """Resolve an existing regular directory without following symlinks."""

    try:
        raw = Path(root).expanduser()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise PathContractError(f"root is invalid: {root}") from exc
    if path_has_symlink_component(raw, raw):
        raise PathContractError(f"root contains a symlink component: {raw}")
    try:
        resolved = raw.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathContractError(f"root could not be resolved: {raw}") from exc
    if not resolved.is_dir() or resolved.is_symlink():
        raise PathContractError(f"root is missing or not a regular directory: {raw}")
    return resolved


def resolve_rooted_locator(
    root: str | Path,
    relative_path: object,
    *,
    kind: str | None = None,
    must_exist: bool = True,
) -> Path:
    """Resolve an existing root-relative file/directory locator.

    ``kind`` may be ``"file"`` or ``"directory"``.  Symlink aliases are
    rejected even when their physical target remains inside the root.
    """

    root_path = trusted_root(root)
    if kind not in {None, "file", "directory"}:
        raise PathContractError(f"rooted locator kind is invalid: {kind}")
    relative = validate_relative_path(relative_path, "rooted locator")
    candidate = root_path / relative
    if path_has_symlink_component(root_path, candidate):
        raise PathContractError(f"rooted locator is a symlink: {relative}")
    try:
        physical = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathContractError(f"rooted locator cannot be resolved: {relative}") from exc
    try:
        contained = physical.is_relative_to(root_path)
    except ValueError:
        contained = False
    if not contained:
        raise PathContractError(f"rooted locator escaped root: {relative}")
    if not must_exist:
        return physical
    if not physical.exists():
        raise PathContractError(f"rooted locator is missing: {relative}")
    if kind == "file" and not physical.is_file():
        raise PathContractError(f"rooted locator is not a file: {relative}")
    if kind == "directory" and not physical.is_dir():
        raise PathContractError(f"rooted locator is not a directory: {relative}")
    if kind is None and not (physical.is_file() or physical.is_dir()):
        raise PathContractError(f"rooted locator is not a regular path: {relative}")
    return physical


def validate_output_path(
    root: str | Path,
    candidate: str | Path,
    *,
    kind: str | None = None,
    must_exist: bool = False,
) -> Path:
    """Validate an output path whose final component may not exist yet."""

    root_path = trusted_root(root)
    try:
        raw_candidate = Path(candidate).expanduser()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise PathContractError(f"output path is invalid: {candidate}") from exc
    path = raw_candidate if raw_candidate.is_absolute() else root_path / raw_candidate
    if path_has_symlink_component(root_path, path):
        raise PathContractError(f"output path contains a symlink component: {path}")
    try:
        physical = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathContractError(f"output path could not be resolved: {path}") from exc
    try:
        contained = physical.is_relative_to(root_path)
    except ValueError:
        contained = False
    if not contained:
        raise PathContractError(f"output path escaped root: {path}")
    if kind not in {None, "file", "directory"}:
        raise PathContractError(f"output path kind is invalid: {kind}")
    if must_exist and not physical.exists():
        raise PathContractError(f"output path is missing: {path}")
    if physical.exists() and kind == "file" and not physical.is_file():
        raise PathContractError(f"output path is not a file: {path}")
    if physical.exists() and kind == "directory" and not physical.is_dir():
        raise PathContractError(f"output path is not a directory: {path}")
    if physical.exists() and kind is None and not (physical.is_file() or physical.is_dir()):
        raise PathContractError(f"output path is not a regular path: {path}")
    return physical


__all__ = [
    "PathContractError",
    "path_has_any_symlink_component",
    "path_has_symlink_component",
    "resolve_rooted_locator",
    "trusted_root",
    "validate_output_path",
    "validate_relative_path",
]
