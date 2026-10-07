"""Publish an immutable pair of control and payload manifests."""

from __future__ import annotations
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping
from .errors import WorkspaceError
from .workspace import SplitRunPaths, stable_digest, write_json_once

from .manifest_artifacts import artifact_index
from .manifest_locators import artifact_locator_index
from .manifest_binding import binding


def publish_split_manifests(
    run: SplitRunPaths,
    *,
    request_digest: str,
    status: str,
    control_details: Mapping[str, Any],
    payload_details: Mapping[str, Any],
    control_artifacts: Iterable[Path] = (),
    payload_artifacts: Iterable[Path] = (),
    control_locators: Iterable[Mapping[str, Any]] = (),
    payload_locators: Iterable[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Publish a mutually bound, immutable manifest pair.

    A literal hash of each complete file inside the other file would be a hash
    cycle.  The binding therefore hashes each manifest's normalized core (the
    complete document except ``binding``) and stores both reciprocal core
    hashes.  Full-file hashes are computed after publication and returned by
    :func:`verify_split_manifests`.
    """
    if run.control_manifest.exists() or run.payload_manifest.exists():
        raise WorkspaceError(f"refusing to replace a split manifest for run {run.run_id}")
    common = {
        "schema_version": 1,
        "run_id": run.run_id,
        "request_digest": request_digest,
        "status": status,
    }
    control_core: dict[str, Any] = {
        **common,
        "kind": "aivw_control_manifest",
        "role": "control",
        "run_dir": str(run.control_root),
        "details": deepcopy(dict(control_details)),
        "artifacts": artifact_index(run.control_root, control_artifacts),
        "artifact_locators": artifact_locator_index(run.control_root, control_locators),
    }
    payload_core: dict[str, Any] = {
        **common,
        "kind": "aivw_payload_manifest",
        "role": "payload",
        "run_dir": str(run.payload_root),
        "details": deepcopy(dict(payload_details)),
        "artifacts": artifact_index(run.payload_root, payload_artifacts),
        "artifact_locators": artifact_locator_index(run.payload_root, payload_locators),
    }
    control_hash = stable_digest(control_core)
    payload_hash = stable_digest(payload_core)
    control = {
        **control_core,
        "binding": binding(
            self_hash=control_hash,
            peer_hash=payload_hash,
            peer_role="payload",
            peer_root=run.payload_root,
            peer_manifest=run.payload_manifest,
        ),
    }
    payload = {
        **payload_core,
        "binding": binding(
            self_hash=payload_hash,
            peer_hash=control_hash,
            peer_role="control",
            peer_root=run.control_root,
            peer_manifest=run.control_manifest,
        ),
    }
    # Write payload first.  A crash between these writes leaves an incomplete
    # pair that verification rejects; it can never be mistaken for a valid run.
    write_json_once(run.payload_manifest, payload)
    write_json_once(run.control_manifest, control)
    return control, payload

