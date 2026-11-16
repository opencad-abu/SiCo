"""Validation and discovery for layout backend manifests."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "sico.layout.backend.v1"
API_VERSION = "1.0"
IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")


class ManifestError(ValueError):
    pass


def _required(value: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    missing = [name for name in fields if name not in value]
    if missing:
        raise ManifestError(f"{label} missing fields: {', '.join(missing)}")


def _strings(value: Any, label: str, *, nonempty: bool = True) -> list[str]:
    if (not isinstance(value, list) or (nonempty and not value)
            or any(not isinstance(item, str) for item in value)):
        raise ManifestError(label + " must be an array of strings")
    return value


def load_manifest(path: str | Path) -> dict[str, Any]:
    path = Path(path).expanduser()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read backend manifest: {path}") from exc
    return validate_manifest(value)


def validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError("backend manifest must be an object")
    _required(
        value,
        ("schema", "id", "name", "version", "api_version", "transport", "capabilities"),
        "manifest",
    )
    if value["schema"] != SCHEMA:
        raise ManifestError("unsupported backend manifest schema")
    if value["api_version"] != API_VERSION:
        raise ManifestError("unsupported backend API version")
    if not isinstance(value["id"], str) or not IDENTIFIER.fullmatch(value["id"]):
        raise ManifestError("invalid backend id")
    for name in ("name", "version"):
        if not isinstance(value[name], str) or not value[name]:
            raise ManifestError("invalid backend " + name)
    transport = value["transport"]
    if not isinstance(transport, dict) or transport.get("kind") != "stdio":
        raise ManifestError("v1 host supports only stdio transport")
    _required(transport, ("command", "cwd"), "transport")
    command = transport["command"]
    if (not isinstance(command, list) or not command
            or any(not isinstance(item, str) or not item for item in command)):
        raise ManifestError("transport.command must be a non-empty string array")
    if not isinstance(transport["cwd"], str) or not Path(transport["cwd"]).is_absolute():
        raise ManifestError("transport.cwd must be absolute")
    capabilities = value["capabilities"]
    if not isinstance(capabilities, dict):
        raise ManifestError("capabilities must be an object")
    _required(capabilities, ("source_kinds", "actions", "process_view_schema"), "capabilities")
    _strings(capabilities["source_kinds"], "capabilities.source_kinds")
    if (not isinstance(capabilities["process_view_schema"], str)
            or not capabilities["process_view_schema"]):
        raise ManifestError("process_view_schema is required")
    actions = capabilities["actions"]
    if not isinstance(actions, dict) or not actions:
        raise ManifestError("capabilities.actions must be a non-empty object")
    for name, action in actions.items():
        if not isinstance(name, str) or not isinstance(action, dict):
            raise ManifestError("invalid action declaration")
        for key in ("requires", "produces", "constraint_kinds"):
            if key in action:
                _strings(action[key], f"actions.{name}.{key}", nonempty=False)
        if "source_kinds" in action:
            _strings(action["source_kinds"], f"actions.{name}.source_kinds")
    if "artifact_formats" in capabilities:
        _strings(capabilities["artifact_formats"], "artifact_formats", nonempty=False)
    if "catalogs" in capabilities:
        _strings(capabilities["catalogs"], "catalogs", nonempty=False)
    if "license" in value and not isinstance(value["license"], dict):
        raise ManifestError("license must be an object")
    if "integrity" in value and not isinstance(value["integrity"], dict):
        raise ManifestError("integrity must be an object")
    return value


def _summary(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return only the non-secret manifest surface exposed to the agent."""
    return {
        "id": manifest["id"],
        "name": manifest["name"],
        "version": manifest["version"],
        "api_version": manifest["api_version"],
        "capabilities": manifest["capabilities"],
        "license": manifest.get("license", {}),
    }


class BackendRegistry:
    """Discover manifests below one project-private backend root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.manifests: dict[str, dict[str, Any]] = {}
        self.errors: list[dict[str, str]] = []
        self.reload()

    def reload(self) -> None:
        manifests: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, str]] = []
        if not self.root.exists():
            self.manifests, self.errors = manifests, errors
            return
        for path in sorted(self.root.glob("*/backend.json")):
            try:
                value = load_manifest(path)
            except ManifestError as exc:
                errors.append({"path": str(path), "message": str(exc)})
                continue
            if value["id"] in manifests:
                errors.append({"path": str(path), "message": "duplicate backend id"})
                continue
            manifests[value["id"]] = value
        self.manifests, self.errors = manifests, errors

    def list(self) -> dict[str, Any]:
        return {
            "backends": [_summary(value) for _, value in sorted(self.manifests.items())],
            "errors": list(self.errors),
        }

    def get(self, backend_id: str) -> dict[str, Any]:
        if backend_id not in self.manifests:
            raise ManifestError("unknown layout backend: " + backend_id)
        return self.manifests[backend_id]

    def info(self, backend_id: str) -> dict[str, Any]:
        return _summary(self.get(backend_id))
