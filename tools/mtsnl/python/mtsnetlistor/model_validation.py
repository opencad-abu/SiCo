"""Primitive OA, path and numeric validation for immutable request values."""

from __future__ import annotations

import math
from pathlib import Path
import re
from typing import Any
from .errors import RequestValidationError


OA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


SIMULATORS = frozenset({"spectre", "hspiceD"})


MTS_DESIGN_CIRCUIT_SECTION = "__mts_design_circuit"


def validate_oa_name(value: str, label: str = "OA name") -> str:
    """Validate a library/cell/view name before it enters argv or SKILL."""

    text = str(value).strip()
    if not text or not OA_NAME_RE.fullmatch(text):
        raise RequestValidationError(
            f"invalid {label}: {value!r}; expected an OA identifier"
        )
    return text


def _path(value: str | Path, label: str, *, file: bool = False) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if file and not path.is_file():
        raise RequestValidationError(f"cannot access {label}: {path}")
    if not file and path.exists() and not path.is_dir():
        raise RequestValidationError(f"{label} is not a directory: {path}")
    return path


def _finite_real(value: Any, label: str, *, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RequestValidationError(f"{label} must be a real number") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        suffix = " greater than zero" if positive else " finite"
        raise RequestValidationError(f"{label} must be{suffix}")
    return number
