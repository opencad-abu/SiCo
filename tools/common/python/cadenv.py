"""Environment boundaries between CAD automation and vendor EDA tools."""

from __future__ import annotations

import os
from typing import Mapping, MutableMapping

from sicoenv import value as environment_value

CAD_TEMP_ENV = "CAD_TEMP_DIR"
SICO_TEMP_ENV = "SICO_TEMP_DIR"
EDA_TEMP_ENV_NAMES = (
    "TMPDIR",
    "TMP",
    "TEMP",
    "SQLITE_TMPDIR",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
)
_LEGACY_ORIGINAL_PREFIX = "CAD_ORIG_"
_ORIGINAL_PREFIX = "SICO_ORIG_"
EDA_TEMP_MARKERS = tuple(
    prefix + name + suffix
    for prefix in (_ORIGINAL_PREFIX, _LEGACY_ORIGINAL_PREFIX)
    for name in EDA_TEMP_ENV_NAMES
    for suffix in ("", "_SET")
)
CADENCE_MPS_PREFIX = "CDS_MPS_"
VIRTUOSO_ENV_NAMES = (
    "PATH", "LD_LIBRARY_PATH", "PYTHONPATH", "MODULEPATH",
    "LOADEDMODULES", "_LMFILES_", "MODULESHOME", "LMOD_CMD",
)
VIRTUOSO_MARKERS = tuple(prefix + name for prefix in ("SICO_VIRTUOSO_", "CAD_VIRTUOSO_")
                        for name in VIRTUOSO_ENV_NAMES)


def captured_virtuoso_environment(environment: Mapping[str, str], *, credential=None):
    """Read only registered EDA provenance; never reinterpret an API credential."""
    captured = {}
    for name in VIRTUOSO_ENV_NAMES:
        current, old = "SICO_VIRTUOSO_" + name, "CAD_VIRTUOSO_" + name
        if credential in (name, current, old):
            continue
        saved = environment_value(environment, current, (old,))
        if saved is not None:
            captured[name] = saved
    return captured


def preserve_virtuoso_environment(environment: MutableMapping[str, str]) -> None:
    """Capture current EDA provenance once before sanitation."""
    captured = captured_virtuoso_environment(environment)
    for name in VIRTUOSO_ENV_NAMES:
        environment["SICO_VIRTUOSO_" + name] = captured.get(name, environment.get(name, ""))
        environment.pop("CAD_VIRTUOSO_" + name, None)


def cadence_mps_environment_names(environment: Mapping[str, str]) -> tuple[str, ...]:
    """Return Cadence multiprocess-session selectors in stable order."""

    return tuple(sorted(name for name in environment if name.startswith(CADENCE_MPS_PREFIX)))


def detach_cadence_mps_environment(
    environment: MutableMapping[str, str],
) -> tuple[str, ...]:
    """Detach an environment from any host Virtuoso MPS session in place."""

    removed = cadence_mps_environment_names(environment)
    for name in removed:
        environment.pop(name, None)
    return removed


def _legacy_injected_value(name: str, cad_root: str) -> str:
    if name == "XDG_CACHE_HOME":
        return os.path.join(cad_root, "cache")
    if name == "XDG_RUNTIME_DIR":
        return os.path.join(cad_root, "runtime")
    return cad_root


def preserve_eda_temp_environment(environment: MutableMapping[str, str]) -> None:
    """Preserve one marker family before redirecting project scratch files."""

    _validate_original_markers(environment)
    cad_root = environment_value(environment, SICO_TEMP_ENV, (CAD_TEMP_ENV,), "").strip()
    prefix = _ORIGINAL_PREFIX
    for name in EDA_TEMP_ENV_NAMES:
        set_marker = f"{prefix}{name}_SET"
        if set_marker in environment:
            continue
        value = environment.get(name)
        if cad_root and value == _legacy_injected_value(name, cad_root):
            environment[set_marker] = "0"
        elif value is None:
            environment[set_marker] = "0"
        else:
            environment[set_marker] = "1"
            environment[f"{prefix}{name}"] = value
    if prefix == _ORIGINAL_PREFIX:
        for name in EDA_TEMP_ENV_NAMES:
            environment.pop(f"{_LEGACY_ORIGINAL_PREFIX}{name}_SET", None)
            environment.pop(f"{_LEGACY_ORIGINAL_PREFIX}{name}", None)


def restore_eda_temp_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Restore vendor EDA temp settings after CAD automation isolation."""

    result = dict(os.environ if environment is None else environment)
    _validate_original_markers(result)
    cad_root = environment_value(result, SICO_TEMP_ENV, (CAD_TEMP_ENV,), "").strip()
    for name in EDA_TEMP_ENV_NAMES:
        # State and value belong to the same writer; never mix marker families.
        prefix = _ORIGINAL_PREFIX
        state = result.get(f"{prefix}{name}_SET")
        original = result.get(f"{prefix}{name}")
        for family in (_ORIGINAL_PREFIX, _LEGACY_ORIGINAL_PREFIX):
            result.pop(f"{family}{name}_SET", None)
            result.pop(f"{family}{name}", None)
        if state == "1":
            result[name] = "" if original is None else original
        elif state == "0":
            result.pop(name, None)
        elif cad_root and result.get(name) == _legacy_injected_value(name, cad_root):
            # Older frontends did not preserve the pre-CAD value. Removing the
            # injected value lets the EDA tool use its normal default again.
            result.pop(name, None)
    result.pop(CAD_TEMP_ENV, None)
    result.pop(SICO_TEMP_ENV, None)
    return result


def _validate_original_markers(environment: Mapping[str, str]) -> None:
    """Reject retired-only provenance before any capture or cleanup can lose it."""
    for name in EDA_TEMP_ENV_NAMES:
        current = f"{_ORIGINAL_PREFIX}{name}_SET"
        retired = (f"{_LEGACY_ORIGINAL_PREFIX}{name}_SET",
                   f"{_LEGACY_ORIGINAL_PREFIX}{name}")
        environment_value(environment, current, retired)
