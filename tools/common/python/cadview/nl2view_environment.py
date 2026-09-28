"""Build the isolated environment used by cdsTextTo5x."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

from cadenv import (
    detach_cadence_mps_environment,
    preserve_eda_temp_environment,
    restore_eda_temp_environment,
)
from sicotemp import selected_state
from sicostate import validate_directory

from .errors import Nl2ViewError


def _temporary_directory(environment: Mapping[str, str]) -> Path:
    try:
        temporary = selected_state(environment, create=True)
        validate_directory(temporary)
    except (OSError, ValueError) as exc:
        raise Nl2ViewError("Invalid SiCo project temporary directory: " + str(exc)) from exc
    return temporary


def import_environment(
    copy_source: bool, environ: Optional[Mapping[str, str]] = None
) -> Tuple[Dict[str, str], Path]:
    """Build an EDA-safe environment and private working directory."""

    environment = dict(os.environ if environ is None else environ)
    temporary = _temporary_directory(environment)
    preserve_eda_temp_environment(environment)
    environment = restore_eda_temp_environment(environment)
    # Standalone and MTS GUI launchers preserve the real Cadence runtime path
    # under different markers. Prefer the session-specific marker when both
    # are present because nested wrappers may set both.
    for marker in (
        "MTS_NETLISTOR_ORIG_LD_LIBRARY_PATH",
        "NL2VIEW_ORIG_LD_LIBRARY_PATH",
    ):
        if marker in environment:
            environment["LD_LIBRARY_PATH"] = environment[marker]
            break
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    environment.pop("LD_PRELOAD", None)
    environment.pop("LD_AUDIT", None)
    detach_cadence_mps_environment(environment)
    if copy_source:
        environment["CDS5X_NOLINK"] = "1"
    else:
        environment.pop("CDS5X_NOLINK", None)
    return environment, temporary
