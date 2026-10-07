"""Pinned Codex core and startup-only tool format selection for both frontends."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping

from .env_names import value as env_value

CODEX_VERSION = "0.156.1"
LEGACY_ENV = "SICO_LEGACY_0114"


class CodexConfigurationError(ValueError):
    """A fixed, credential-free diagnostic safe to display at startup."""


def require_supported_runtime(environment: Mapping[str, str] | None = None) -> None:
    source = os.environ if environment is None else environment
    value = str(env_value(source, "LEGACY_0114", "")).strip()
    if value not in {"", "0", "1"}:
        raise CodexConfigurationError(
            "SICO_LEGACY_0114 must be unset, 0 or 1; it selects a tool wrapper, "
            "never a different Codex version"
        )


def codex_tool_format(environment: Mapping[str, str], configured: str | None = None) -> str:
    require_supported_runtime(environment)
    selected = (
        str(env_value(environment, "TOOL_FORMAT", "native")) if configured is None else configured
    )
    if selected not in ("native", "flat"):
        raise CodexConfigurationError("Codex tool format must be native or flat")
    switch = str(env_value(environment, "LEGACY_0114", "")).strip()
    return {"1": "flat", "0": "native"}.get(switch, selected)


def verify_codex_version(executable: str, environment: Mapping[str, str]) -> None:
    require_supported_runtime(environment)
    expected = CODEX_VERSION
    # A version probe needs no model credentials or host application variables.
    probe_environment = {
        name: environment[name]
        for name in (
            "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL",
            "LD_LIBRARY_PATH", "TMPDIR", "TMP", "TEMP",
        )
        if name in environment
    }
    try:
        result = subprocess.run(
            [executable, "--version"],
            env=probe_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        raise CodexConfigurationError("Codex executable failed its version probe") from None
    if result.returncode or result.stdout.strip() != ("codex-cli " + expected).encode("ascii"):
        raise CodexConfigurationError(
            "Selected Codex executable must report codex-cli " + expected
            + "; check the configured CLI path"
        )
