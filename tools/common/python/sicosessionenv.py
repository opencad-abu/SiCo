"""Finite environment handoff for delegated AI session credentials.

Retired CAD_AI/CAD_CODEX names are rejected or cleared, never read.
"""

from sicoenv import value as environment_value

FIELDS = ("RUNTIME", "SOCKET", "SPOOL", "TOKEN", "WORKSPACE", "PROFILE")


def names(field):
    if field not in FIELDS:
        raise ValueError("Unknown session environment field")
    return ("SICO_AI_" + field, "CAD_AI_" + field, "CAD_CODEX_" + field)


ENVIRONMENT_NAMES = tuple(name for field in FIELDS for name in names(field))


def value(environment, field, default=None):
    current, *retired = names(field)
    return environment_value(environment, current, tuple(retired), default)


def consume(environment):
    """Remove all aliases, including credentials shadowed by a newer producer."""
    try:
        return {field: value(environment, field) for field in FIELDS}
    finally:
        clear(environment)


def clear(environment):
    for name in ENVIRONMENT_NAMES:
        environment.pop(name, None)


def publish(environment, **fields):
    """Replace a complete delegated handoff without retaining stale credentials."""
    if set(fields) - set(FIELDS):
        raise ValueError("Unknown session environment field")
    clear(environment)
    environment.update({names(field)[0]: str(value) for field, value in fields.items()
                        if value is not None})
