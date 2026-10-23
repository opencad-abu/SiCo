"""Bind optional data paths to a workspace without gating agent startup."""

import os
from pathlib import Path

from sicoenv import RETIRED_NAMES, value

from .env_names import (
    CIRCUIT_TEMPLATES_ENV,
    CIRCUIT_TEMPLATES_LEGACY,
    resolved_name,
)
from .env_names import (
    value as environment_value,
)

PATH_NAMES = ("SICO_CIRCUIT_TEMPLATES_DIR", "SICO_PDK_DATA",
              "SICO_AI_DEVICE_CATALOG", "SICO_AI_SKILL_REFERENCE_ROOT")
ENVIRONMENT_KEYS = (*PATH_NAMES, "SICO_PDK_WORKFLOW",
                    *(old for name in PATH_NAMES for old in RETIRED_NAMES.get(name, ())))
DATA_SOURCE_ENV = {"pdk_data": "SICO_PDK_DATA",
                   "circuit_templates": "SICO_CIRCUIT_TEMPLATES_DIR"}


def forwarded(environment):
    """Retain retired spellings only for a deferred missing-migration diagnostic."""
    result = {name: environment[name] for name in ENVIRONMENT_KEYS if name in environment}
    for name in PATH_NAMES:
        if name in result:
            for old in RETIRED_NAMES.get(name, ()):
                result.pop(old, None)
    return result


def resource_path(configured, variable, *, workspace=None):
    """Interpret relative data configuration only against the caller's workspace."""
    if not isinstance(configured, str) or not configured.strip() or len(configured) > 4096:
        raise ValueError(variable + " must name one path")
    if any(ord(char) < 32 or ord(char) == 127 for char in configured):
        raise ValueError(variable + " contains control characters")
    path = Path(configured.strip()).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() if workspace is None else Path(workspace)) / path
    return path.resolve()


def prepare(environment, workspace):
    """Freeze valid paths; retain bad values for diagnostics in the owning tool.

    No resource is opened, created or fetched during this startup preparation.
    Nonempty retired names are diagnostic inputs only, never fallback values.
    """
    warnings = []
    for name in PATH_NAMES:
        try:
            configured = value(environment, name, RETIRED_NAMES.get(name, ()))
            if configured:
                environment[name] = str(resource_path(configured, name, workspace=workspace))
        except (OSError, RuntimeError, ValueError) as exc:
            warnings.append(str(exc) + "; correct this optional resource before using its tools")
    return tuple(warnings)


def _configured(environment, kind):
    if kind == "pdk_data":
        return environment_value(environment, "PDK_DATA", "")
    if kind == "circuit_templates":
        name = resolved_name(environment, CIRCUIT_TEMPLATES_ENV, CIRCUIT_TEMPLATES_LEGACY)
        return environment.get(name, "")
    raise KeyError(kind)


def _availability(kind, path):
    try:
        value = Path(path)
        if not value.exists():
            return "missing", "路径不存在"
        if kind == "circuit_templates" and not value.is_dir():
            return "invalid_type", "模板数据必须是目录"
        if kind == "pdk_data" and not (value.is_file() or value.is_dir()):
            return "invalid_type", "工艺数据必须是文件或目录"
        mode = os.R_OK | (os.X_OK if value.is_dir() else 0)
        if not os.access(value, mode):
            return "unreadable", "路径不可读"
        return "available", ""
    except (OSError, ValueError, RuntimeError):
        return "unreadable", "路径不可读"


def project_data_sources(workspace):
    """Read the project override document; callers report invalid files separately."""
    from sico.agent_resources.data_sources import read

    return read(workspace)


def effective_data_sources(environment, workspace, *, project=None):
    """Resolve project overrides before environment defaults for each data root."""
    if project is None:
        try:
            project = project_data_sources(workspace)
        except (ValueError, OSError, RuntimeError):
            project = {}
    result = {}
    for kind, name in DATA_SOURCE_ENV.items():
        override = project.get(kind)
        source, configured = ("project", override) if override else ("environment", None)
        if not override:
            try:
                configured = str(_configured(environment, kind)).strip()
            except (ValueError, OSError, RuntimeError) as exc:
                result[kind] = {"path": None, "source": "environment",
                                "environment": name, "status": "invalid",
                                "message": str(exc)[:512]}
                continue
        if not configured:
            result[kind] = {"path": None, "source": "none", "environment": name,
                            "status": "unconfigured", "message": "未配置"}
            continue
        source = "project" if override else "environment"
        try:
            path = resource_path(configured, name, workspace=workspace)
        except (OSError, RuntimeError, ValueError) as exc:
            result[kind] = {"path": configured, "source": source,
                            "environment": name, "status": "invalid",
                            "message": str(exc)[:512]}
            continue
        status, message = _availability(kind, path)
        result[kind] = {"path": str(path), "source": source, "environment": name,
                        "status": status, "message": message}
    return result


def apply_data_sources(environment, workspace, *, source=None):
    """Write the effective structured-data roots into a child environment."""
    values = effective_data_sources(environment if source is None else source, workspace)
    for kind, name in DATA_SOURCE_ENV.items():
        path = values[kind]["path"]
        if path:
            environment[name] = path
    return values
