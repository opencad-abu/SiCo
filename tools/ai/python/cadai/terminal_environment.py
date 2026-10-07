"""Locale and X11 input policy for the external AI terminal."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from sicoenv import value as environment_value

from .lsf import LSF_HANDOFF_ENV, LSF_SUBMIT_DISPLAY_ENV, LSF_METADATA_ALIASES

_UTF8_LOCALE_ENV = "SICO_AI_UTF8_LOCALE"
_UTF8_LOCALE_FALLBACKS = (
    "C.UTF-8",
    "C.utf8",
    "en_US.UTF-8",
    "en_US.utf8",
    "zh_CN.UTF-8",
    "zh_CN.utf8",
)
_LSF_HANDOFF_METADATA = (*LSF_METADATA_ALIASES, *LSF_METADATA_ALIASES.values())


def _configured_locale(environment):
    return environment_value(environment, _UTF8_LOCALE_ENV, ("CAD_AI_UTF8_LOCALE",), "").strip()


def _is_utf8_locale(value: str) -> bool:
    return "utf8" in value.lower().replace("-", "")


def _locale_executable() -> str | None:
    for candidate in (Path("/usr/bin/locale"), Path("/bin/locale")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _locale_supports_utf8(value: str, environment: Mapping[str, str]) -> bool:
    executable = _locale_executable()
    if executable is None or not value or "\0" in value:
        return False
    probe_environment = {"LANG": "C", "LC_ALL": value}
    if locpath := environment.get("LOCPATH", "").strip():
        probe_environment["LOCPATH"] = locpath
    try:
        completed = subprocess.run(
            [executable, "charmap"],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="ascii",
            errors="replace",
            env=probe_environment,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and _is_utf8_locale(completed.stdout.strip())


def _select_utf8_locale(
    environment: Mapping[str, str], probe: Callable[[str], bool] | None = None
) -> str:
    configured = _configured_locale(environment)
    if configured:
        if "\0" in configured or not _is_utf8_locale(configured):
            raise ValueError(f"{_UTF8_LOCALE_ENV} must name a UTF-8 locale")
        supported = probe or (lambda value: _locale_supports_utf8(value, environment))
        if supported(configured):
            return configured
        raise RuntimeError(f"{_UTF8_LOCALE_ENV} is not available on this host")

    candidates: list[str] = []
    for name in ("LC_ALL", "LC_CTYPE", "LANG"):
        value = environment.get(name, "").strip()
        if value and "\0" not in value and _is_utf8_locale(value):
            candidates.append(value)
    candidates.extend(_UTF8_LOCALE_FALLBACKS)

    supported = probe or (lambda value: _locale_supports_utf8(value, environment))
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen and supported(candidate):
            return candidate
        seen.add(candidate)
    raise RuntimeError(
        "no usable UTF-8 locale is installed; set SICO_AI_UTF8_LOCALE to an "
        "available locale reported by 'locale -a'"
    )


def _apply_utf8_ctype(environment: dict[str, str]) -> None:
    selected = _select_utf8_locale(environment)
    configured = bool(_configured_locale(environment))
    effective = (
        environment.get("LC_ALL", "").strip()
        or environment.get("LC_CTYPE", "").strip()
        or environment.get("LANG", "").strip()
    )
    if configured or effective != selected:
        environment.pop("LC_ALL", None)
        environment["LC_CTYPE"] = selected


def _is_local_display(value: str) -> bool:
    display = value.strip().lower()
    return display.startswith((":", "unix:", "localhost:", "127.0.0.1:", "[::1]:"))


def _lsf_handoff_preflight(environment: Mapping[str, str]) -> tuple[str, ...]:
    if environment_value(environment, LSF_HANDOFF_ENV,
                         (LSF_METADATA_ALIASES[LSF_HANDOFF_ENV],)) != "1":
        return ()
    display = environment.get("DISPLAY", "").strip()
    if not display:
        raise RuntimeError(
            "LSF AI session has no DISPLAY; configure the site handoff for X11 "
            "forwarding (standard bsub normally needs -XF)"
        )

    warnings: list[str] = []
    submitted_display = environment_value(environment, LSF_SUBMIT_DISPLAY_ENV,
        (LSF_METADATA_ALIASES[LSF_SUBMIT_DISPLAY_ENV],), "").strip()
    if submitted_display == display and _is_local_display(display):
        warnings.append(
            "LSF did not rewrite a host-local DISPLAY; verify that the job uses "
            "working X11 forwarding"
        )
    if xauthority := environment.get("XAUTHORITY", "").strip():
        authority = Path(xauthority)
        if not authority.is_file() or not os.access(authority, os.R_OK):
            warnings.append("XAUTHORITY is set but is not a readable regular file")
    return tuple(warnings)


def prepare_input_environment(
    environment: dict[str, str],
) -> tuple[str, ...]:
    """Validate locale/X11 inputs without changing desktop input-method state."""
    warnings = list(_lsf_handoff_preflight(environment))
    _apply_utf8_ctype(environment)
    for name in _LSF_HANDOFF_METADATA:
        environment.pop(name, None)
    return tuple(warnings)


__all__ = ["prepare_input_environment"]
