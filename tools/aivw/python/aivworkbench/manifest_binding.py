"""Read and validate reciprocal manifest identities and bindings."""

from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping
from .errors import WorkspaceError



_BINDING_VERSION = 1


def binding(
    *,
    self_hash: str,
    peer_hash: str,
    peer_role: str,
    peer_root: Path,
    peer_manifest: Path,
) -> dict[str, object]:
    return {
        "version": _BINDING_VERSION,
        "algorithm": "sha256",
        "normalization": "canonical-json-without-binding",
        "self_core_sha256": self_hash,
        "peer": {
            "role": peer_role,
            "root_path": str(peer_root),
            "root_uri": peer_root.resolve().as_uri(),
            "manifest_path": str(peer_manifest),
            "manifest_uri": peer_manifest.resolve().as_uri(),
            "manifest_core_sha256": peer_hash,
        },
    }


def read_manifest(path: Path, role: str) -> dict[str, Any]:
    value = read_json_object(path, role)
    if value.get("schema_version") != 1:
        raise WorkspaceError(f"invalid {role} manifest schema: {path}")
    expected_kind = f"aivw_{role}_manifest"
    if value.get("role") != role or value.get("kind") != expected_kind:
        raise WorkspaceError(f"invalid {role} manifest identity: {path}")
    if not isinstance(value.get("artifacts"), list):
        raise WorkspaceError(f"invalid {role} artifact index: {path}")
    locators = value.get("artifact_locators", [])
    if not isinstance(locators, list):
        raise WorkspaceError(f"invalid {role} artifact locator index: {path}")
    return value


def read_json_object(path: Path, role: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"cannot read {role} manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkspaceError(f"invalid {role} manifest root: {path}")
    return value


def manifest_core(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(item) for key, item in value.items() if key != "binding"}


def verify_binding(
    value: Mapping[str, Any],
    *,
    self_hash: str,
    peer_hash: str,
    peer_role: str,
    peer_manifest: Path,
) -> None:
    binding = value.get("binding")
    if not isinstance(binding, Mapping):
        raise WorkspaceError(f"{value.get('role')} manifest has no binding")
    if (
        binding.get("version") != _BINDING_VERSION
        or binding.get("algorithm") != "sha256"
        or binding.get("normalization") != "canonical-json-without-binding"
        or binding.get("self_core_sha256") != self_hash
    ):
        raise WorkspaceError(f"{value.get('role')} manifest self binding mismatch")
    peer = binding.get("peer")
    if not isinstance(peer, Mapping):
        raise WorkspaceError(f"{value.get('role')} manifest peer binding is invalid")
    if peer.get("role") != peer_role or peer.get("manifest_core_sha256") != peer_hash:
        raise WorkspaceError(f"{value.get('role')} manifest peer hash mismatch")
    if peer.get("manifest_uri") != peer_manifest.as_uri():
        raise WorkspaceError(f"{value.get('role')} manifest peer locator mismatch")
    raw_path = peer.get("manifest_path")
    if not isinstance(raw_path, str) or Path(raw_path).expanduser().resolve() != peer_manifest:
        raise WorkspaceError(f"{value.get('role')} manifest peer path mismatch")

