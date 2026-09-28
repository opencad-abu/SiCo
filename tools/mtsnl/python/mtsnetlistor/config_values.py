"""Strict TOML values and paths relative to the document directory."""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any, Mapping, Optional
from .errors import RequestValidationError


_ENV = re.compile(r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))")


def _table(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RequestValidationError(f"[{label}] must be a table")
    return value


def _strict_keys(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RequestValidationError(f"unknown key(s) in {label}: {', '.join(unknown)}")


def _required_text(value: Mapping[str, Any], key: str, label: str) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise RequestValidationError(f"{label}.{key} must be a non-empty string")
    return raw.strip()


def _config_path(raw: Any, base: Path, label: str, *, required: bool = True) -> Optional[Path]:
    if raw is None or str(raw).strip() == "":
        if required:
            raise RequestValidationError(f"{label} must be set")
        return None
    if not isinstance(raw, str):
        raise RequestValidationError(f"{label} must be a path string")
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group("braced") or match.group("plain")
        if name not in os.environ:
            missing.append(name)
            return match.group(0)
        return os.environ[name]

    expanded = _ENV.sub(replace, raw).replace("$(", "$(")
    if missing:
        raise RequestValidationError(f"undefined environment variable(s) in {label}: {', '.join(sorted(set(missing)))}")
    path = Path(os.path.expanduser(expanded))
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise RequestValidationError(f"{label} must be a boolean")
    return value
