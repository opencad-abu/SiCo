"""Verify a complete control and payload evidence pair."""

from __future__ import annotations
from pathlib import Path
from .errors import WorkspaceError
from .workspace import sha256_file, stable_digest

from .manifest_artifacts import verify_indexed_files
from .manifest_locators import verify_artifact_locators
from .manifest_binding import manifest_core, read_manifest, verify_binding


def verify_split_manifests(
    control_manifest: Path,
    payload_manifest: Path,
    *,
    verify_artifacts: bool = True,
) -> dict[str, object]:
    """Fail closed unless both manifests, bindings, and indexed files agree."""
    control_path = control_manifest.expanduser().resolve()
    payload_path = payload_manifest.expanduser().resolve()
    control = read_manifest(control_path, "control")
    payload = read_manifest(payload_path, "payload")
    for key in ("run_id", "request_digest", "status"):
        if control.get(key) != payload.get(key):
            raise WorkspaceError(f"split manifest {key} mismatch")
    control_core_hash = stable_digest(manifest_core(control))
    payload_core_hash = stable_digest(manifest_core(payload))
    verify_binding(
        control,
        self_hash=control_core_hash,
        peer_hash=payload_core_hash,
        peer_role="payload",
        peer_manifest=payload_path,
    )
    verify_binding(
        payload,
        self_hash=payload_core_hash,
        peer_hash=control_core_hash,
        peer_role="control",
        peer_manifest=control_path,
    )
    counts = {"control": len(control["artifacts"]), "payload": len(payload["artifacts"])}
    locator_counts = {
        "control": len(control.get("artifact_locators", [])),
        "payload": len(payload.get("artifact_locators", [])),
    }
    if verify_artifacts:
        verify_indexed_files(control_path.parent, control["artifacts"], "control")
        verify_indexed_files(payload_path.parent, payload["artifacts"], "payload")
        verify_artifact_locators(
            control_path.parent, control.get("artifact_locators", []), "control"
        )
        verify_artifact_locators(
            payload_path.parent, payload.get("artifact_locators", []), "payload"
        )
    return {
        "status": "PASS",
        "run_status": control["status"],
        "run_id": control["run_id"],
        "request_digest": control["request_digest"],
        "control_manifest": str(control_path),
        "control_manifest_sha256": sha256_file(control_path),
        "control_core_sha256": control_core_hash,
        "payload_manifest": str(payload_path),
        "payload_manifest_sha256": sha256_file(payload_path),
        "payload_core_sha256": payload_core_hash,
        "artifact_counts": counts,
        "locator_counts": locator_counts,
        "artifacts_verified": verify_artifacts,
    }

