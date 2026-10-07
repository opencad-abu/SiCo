"""Bind optional data paths to a workspace without gating agent startup."""

from pathlib import Path

from sicoenv import RETIRED_NAMES, value

PATH_NAMES = ("SICO_CIRCUIT_TEMPLATES_DIR", "SICO_PDK_DATA",
              "SICO_AI_DEVICE_CATALOG", "SICO_AI_SKILL_REFERENCE_ROOT")
ENVIRONMENT_KEYS = (*PATH_NAMES, "SICO_PDK_WORKFLOW",
                    *(old for name in PATH_NAMES for old in RETIRED_NAMES.get(name, ())))


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
