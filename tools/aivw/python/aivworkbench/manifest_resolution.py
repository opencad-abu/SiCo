"""Resolve indexed artifacts through verified manifest bindings."""

from __future__ import annotations
from pathlib import Path
from typing import Mapping
from .artifact_paths import PathContractError, path_has_symlink_component, trusted_root
from .errors import WorkspaceError
from .workspace import sha256_file

from .manifest_records import ResolvedArtifact, ResolvedArtifactLocator
from .manifest_artifacts import safe_relative_path
from .manifest_locators import normalize_locator
from .manifest_binding import read_json_object, read_manifest
from .manifest_verification import verify_split_manifests


def resolve_indexed_artifact(manifest: Path, relative_path: str) -> ResolvedArtifact:
    """Resolve one indexed artifact from a legacy or split-storage manifest.

    Split runs are always entered through their control manifest.  The peer
    payload manifest is verified before its index is trusted.
    """
    source = manifest.expanduser().resolve()
    payload = read_json_object(source, "source")
    artifact_manifest = source
    artifact_payload = payload
    storage_role = "legacy"
    producer_kind = str(payload.get("kind", ""))
    if payload.get("kind") == "aivw_control_manifest" and payload.get("role") == "control":
        binding = payload.get("binding")
        peer = binding.get("peer") if isinstance(binding, Mapping) else None
        raw_peer = peer.get("manifest_path") if isinstance(peer, Mapping) else None
        if not isinstance(raw_peer, str) or not raw_peer:
            raise WorkspaceError("control manifest has no payload manifest locator")
        artifact_manifest = Path(raw_peer).expanduser().resolve()
        verify_split_manifests(source, artifact_manifest)
        artifact_payload = read_manifest(artifact_manifest, "payload")
        storage_role = "payload"
        details = payload.get("details")
        if isinstance(details, Mapping):
            producer_kind = str(details.get("producer_kind", producer_kind))
    relative_text = safe_relative_path(relative_path, "manifest artifact")
    relative = Path(relative_text)
    records = artifact_payload.get("artifacts")
    if not isinstance(records, list):
        raise WorkspaceError("source manifest has no artifact index")
    matches = [
        item
        for item in records
        if isinstance(item, Mapping) and str(item.get("path", "")) == relative.as_posix()
    ]
    if len(matches) != 1:
        raise WorkspaceError(
            f"manifest artifact must have exactly one index record: {relative_text}"
        )
    record = matches[0]
    try:
        root = trusted_root(artifact_manifest.parent)
    except PathContractError as exc:
        raise WorkspaceError(str(exc)) from exc
    candidate = root / relative
    path = candidate.resolve()
    if path_has_symlink_component(root, candidate) or not path.is_file() or not path.is_relative_to(root):
        raise WorkspaceError(f"manifest artifact is missing or unsafe: {relative_text}")
    digest = sha256_file(path)
    if record.get("sha256") != digest or record.get("size") != path.stat().st_size:
        raise WorkspaceError(f"manifest artifact hash mismatch: {relative_text}")
    return ResolvedArtifact(
        source_manifest=source,
        artifact_manifest=artifact_manifest,
        artifact_root=root,
        path=path,
        relative_path=relative_text,
        sha256=digest,
        size=path.stat().st_size,
        producer_kind=producer_kind,
        storage_role=storage_role,
    )


def resolve_indexed_locator(manifest: Path, relative_path: str) -> ResolvedArtifactLocator:
    """Resolve and revalidate one file/directory locator from a manifest."""
    source = manifest.expanduser().resolve()
    payload = read_json_object(source, "source")
    artifact_manifest = source
    artifact_payload = payload
    storage_role = "legacy"
    producer = str(payload.get("kind", ""))
    if payload.get("kind") == "aivw_control_manifest" and payload.get("role") == "control":
        binding = payload.get("binding")
        peer = binding.get("peer") if isinstance(binding, Mapping) else None
        raw_peer = peer.get("manifest_path") if isinstance(peer, Mapping) else None
        if not isinstance(raw_peer, str) or not raw_peer:
            raise WorkspaceError("control manifest has no payload manifest locator")
        artifact_manifest = Path(raw_peer).expanduser().resolve()
        verify_split_manifests(source, artifact_manifest)
        artifact_payload = read_manifest(artifact_manifest, "payload")
        storage_role = "payload"
        details = payload.get("details")
        if isinstance(details, Mapping):
            producer = str(details.get("producer_kind", producer))
    relative = safe_relative_path(relative_path, "manifest artifact locator")
    records = artifact_payload.get("artifact_locators", [])
    if not isinstance(records, list):
        raise WorkspaceError("source manifest has no artifact locator index")
    matches = [
        item for item in records
        if isinstance(item, Mapping) and item.get("path") == relative
    ]
    if len(matches) != 1:
        raise WorkspaceError(
            f"manifest artifact locator must have exactly one index record: {relative}"
        )
    try:
        artifact_root = trusted_root(artifact_manifest.parent)
    except PathContractError as exc:
        raise WorkspaceError(str(exc)) from exc
    record, path = normalize_locator(artifact_root, matches[0])
    return ResolvedArtifactLocator(
        source_manifest=source,
        artifact_manifest=artifact_manifest,
        artifact_root=artifact_root,
        path=path,
        relative_path=relative,
        kind=str(record["kind"]),
        exists=bool(record["exists"]),
        producer=str(record["producer"]),
        sha256=record.get("sha256") if isinstance(record.get("sha256"), str) else None,
        size=record.get("size") if isinstance(record.get("size"), int) else None,
        storage_role=storage_role,
    )

