"""Resolve ``$CWD/.aivw`` and allocate immutable run bundles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Mapping
from urllib.parse import quote

from .errors import WorkspaceError


@dataclass(frozen=True)
class LaunchPaths:
    logical_cwd: Path
    physical_cwd: Path
    logical_artifact_root: Path
    physical_artifact_root: Path


@dataclass(frozen=True)
class RunPaths:
    run_id: str
    root: Path
    manifest: Path


@dataclass(frozen=True)
class SplitRunPaths:
    run_id: str
    control_root: Path
    payload_root: Path
    control_manifest: Path
    payload_manifest: Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_digest(value: object) -> str:
    """Return the historical AIVW JSON-object digest.

    ``ensure_ascii=True`` and Python's legacy non-finite float handling are
    part of persisted run and evidence identifiers.  Do not replace this
    format with a stricter or UTF-8-native serializer in place.
    """
    text = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_launch_paths(
    cwd: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> LaunchPaths:
    environment = os.environ if environ is None else environ
    if cwd is not None:
        logical = Path(cwd).expanduser()
        if not logical.is_absolute():
            logical = Path.cwd() / logical
    else:
        physical_now = Path.cwd().resolve()
        candidate = Path(str(environment.get("PWD", ""))).expanduser()
        logical = (
            candidate
            if candidate.is_absolute() and candidate.resolve() == physical_now
            else physical_now
        )
    physical = logical.resolve()
    if not physical.is_dir():
        raise WorkspaceError(f"launch working directory is unavailable: {logical}")
    logical_root = logical / ".aivw"
    return LaunchPaths(
        logical_cwd=logical,
        physical_cwd=physical,
        logical_artifact_root=logical_root,
        physical_artifact_root=logical_root.resolve(strict=False),
    )


def allocate_run(paths: LaunchPaths, kind: str, request_digest: str) -> RunPaths:
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", kind):
        raise WorkspaceError(f"invalid run kind: {kind!r}")
    if not re.fullmatch(r"[0-9a-f]{64}", request_digest):
        raise WorkspaceError("request digest must be 64 lowercase hexadecimal characters")
    runs = paths.logical_artifact_root / "runs"
    try:
        runs.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspaceError(f"cannot create artifact root {runs}: {exc}") from exc
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    stem = f"{stamp}-{kind}-{request_digest[:12]}"
    for attempt in range(100):
        run_id = stem if attempt == 0 else f"{stem}-{attempt:02d}"
        root = runs / run_id
        try:
            root.mkdir(exist_ok=False)
            return RunPaths(run_id=run_id, root=root, manifest=root / "manifest.json")
        except FileExistsError:
            continue
        except OSError as exc:
            raise WorkspaceError(f"cannot allocate run under {runs}: {exc}") from exc
    raise WorkspaceError(f"cannot allocate a unique run under {runs}")


def resolve_project_db_root(
    configured: str | Path,
    launch: LaunchPaths,
    *,
    forbidden_roots: tuple[Path, ...] = (),
) -> Path:
    root = Path(configured).expanduser()
    if not root.is_absolute():
        raise WorkspaceError(f"PROJ_AMS_DB_DIR must be absolute: {root}")
    physical = root.resolve(strict=False)
    if "smic28" in str(physical).lower():
        raise WorkspaceError("PROJ_AMS_DB_DIR must not use the excluded smic28 workspace")
    blocked = (launch.physical_artifact_root, *forbidden_roots)
    for candidate in blocked:
        resolved = candidate.resolve(strict=False)
        if physical == resolved or physical.is_relative_to(resolved):
            raise WorkspaceError(f"PROJ_AMS_DB_DIR is inside a forbidden root: {resolved}")
    existing = physical
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    if not existing.is_dir() or not os.access(existing, os.W_OK | os.X_OK):
        raise WorkspaceError(f"PROJ_AMS_DB_DIR has no writable parent: {physical}")
    if physical.exists() and not physical.is_dir():
        raise WorkspaceError(f"PROJ_AMS_DB_DIR is not a directory: {physical}")
    return physical


def target_directory_name(library: str, cell: str, view: str) -> str:
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_$+-"
    components = []
    for label, value in (("library", library), ("cell", cell), ("view", view)):
        if not value or len(value) > 255 or any(ord(character) < 32 for character in value):
            raise WorkspaceError(f"invalid target {label}: {value!r}")
        components.append(quote(value, safe=safe).replace(".", "%2E"))
    return ".".join(components)


def allocate_split_run(
    paths: LaunchPaths,
    project_db_root: Path,
    *,
    library: str,
    cell: str,
    view: str,
    kind: str,
    request_digest: str,
) -> SplitRunPaths:
    control = allocate_run(paths, kind, request_digest)
    target = target_directory_name(library, cell, view)
    payload_runs = project_db_root / target / "runs"
    payload: Path | None = None
    try:
        payload_runs.mkdir(parents=True, exist_ok=True)
        payload = payload_runs / control.run_id
        payload.mkdir(exist_ok=False)
    except OSError as exc:
        # The control directory was allocated by this call and is still empty.
        # Do not leave an apparently usable half-run when payload allocation
        # fails.  ``rmdir`` is intentionally used instead of recursive removal.
        try:
            control.root.rmdir()
        except OSError:
            pass
        raise WorkspaceError(f"cannot allocate payload run under {payload_runs}: {exc}") from exc
    assert payload is not None
    physical_payload = payload.resolve()
    if not physical_payload.is_relative_to(project_db_root.resolve()):
        try:
            payload.rmdir()
            control.root.rmdir()
        except OSError:
            pass
        raise WorkspaceError(f"payload run escaped PROJ_AMS_DB_DIR: {physical_payload}")
    return SplitRunPaths(
        run_id=control.run_id,
        control_root=control.root,
        payload_root=payload,
        control_manifest=control.manifest,
        payload_manifest=payload / "payload-manifest.json",
    )


def write_json_once(path: Path, value: object) -> None:
    if path.exists():
        raise WorkspaceError(f"refusing to replace immutable artifact: {path}")
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    except OSError as exc:
        raise WorkspaceError(f"cannot publish immutable artifact {path}: {exc}") from exc
    finally:
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)
