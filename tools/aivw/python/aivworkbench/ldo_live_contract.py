"""Shared LDO live target and worker-output contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .errors import ProfileError, WorkspaceError
from .profiles import Profile


LDO_CELLS = ("LDO_MASTER", "LDO_AON")
LDO_PORTS = {
    "LDO_MASTER": ("VDD", "VSS", "VOUT"),
    "LDO_AON": ("VDD", "VSS", "VOUT", "EN"),
}


def project(profile: Profile) -> Mapping[str, Any]:
    project_value = profile.projects.get("ldo")
    if not isinstance(project_value, Mapping):
        raise ProfileError("profile has no ldo project mapping")
    return project_value


def validate_worker_output(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    """Validate and return a worker object without changing its fields."""

    if value.get("schema_version") != 1:
        raise WorkspaceError(f"{label} worker output has an unsupported schema")
    return dict(value)


def read_worker_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkspaceError(
            f"{label} worker output is not valid JSON: {path}"
        ) from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise WorkspaceError(f"{label} worker output has an unsupported schema: {path}")
    return value


__all__ = [
    "LDO_CELLS",
    "LDO_PORTS",
    "project",
    "read_worker_json",
    "validate_worker_output",
]
