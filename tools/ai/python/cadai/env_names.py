"""Silicon Copilot environment names.

The product variables use the ``SICO_`` prefix. Retired ``CAD_AGENT_``
spellings are diagnostic names only; they never supply current settings.
"""

from __future__ import annotations

from sicoenv import RETIRED_NAMES, value as environment_value

PREFIX = "SICO_"
LEGACY_PREFIX = "CAD_AGENT_"
SUFFIXES = frozenset({
    "API_KEY", "API_URL", "API_BASE_URL", "MODEL", "BACKEND", "PROVIDER", "PROVIDER_CONFIG",
    "CODEX_CLI", "EXTERNAL_RESOURCES", "LEGACY_0114", "LINUX_SANDBOX",
    "MODEL_IDLE_TIMEOUT", "PDK_DATA", "PDK_WORKFLOW", "PDK_DBACCESS", "PDK_WORKERS",
    "PDK_WORKER_CONTEXT", "PDK_WORKER_INIT", "PDK_WORKER_TIMEOUT", "TOOL_FORMAT",
})


def _registered(suffix):
    if suffix not in SUFFIXES:
        raise ValueError("Unregistered SiCo setting: " + suffix)
    return suffix



def name(suffix: str) -> str:
    """Current variable name, for example ``SICO_API_KEY``."""

    return PREFIX + _registered(suffix)


def legacy_name(suffix: str) -> str:
    """Pre-rename variable name, for example ``CAD_AGENT_API_KEY``."""

    return LEGACY_PREFIX + _registered(suffix)


def value(environment, suffix: str, default=None):
    """Read the current variable, rejecting a retired-only spelling."""

    if suffix in {"API_URL", "API_BASE_URL"}:
        # API_URL is the deployed shared spelling; keep this one-way alias until
        # site modulefiles have migrated to API_BASE_URL. Never mix two endpoints.
        if "SICO_API_BASE_URL" in environment:
            current = environment["SICO_API_BASE_URL"]
            alias = environment.get("SICO_API_URL", current)
            if str(current).strip().rstrip("/") != str(alias).strip().rstrip("/"):
                raise ValueError("SICO_API_BASE_URL conflicts with SICO_API_URL; keep one shared endpoint")
            return current
        suffix = "API_URL"
    return environment_value(environment, name(suffix),
                             (legacy_name(suffix),), default)


# Data-path variables whose older spellings never used the CAD_AGENT_ prefix.
# The SICO_* name wins and the earlier names are retained only for removal diagnostics.
CIRCUIT_TEMPLATES_ENV = "SICO_CIRCUIT_TEMPLATES_DIR"
CIRCUIT_TEMPLATES_LEGACY = RETIRED_NAMES[CIRCUIT_TEMPLATES_ENV]
DEVICE_CATALOG_ENV = "SICO_AI_DEVICE_CATALOG"
DEVICE_CATALOG_LEGACY = RETIRED_NAMES[DEVICE_CATALOG_ENV]


def resolved_name(environment, current: str, legacy: tuple = ()) -> str:
    """Return the variable name that carries the value; the current name wins."""

    environment_value(environment, current, legacy)
    return current
