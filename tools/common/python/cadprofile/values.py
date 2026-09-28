"""Profile value normalization and immutable document values."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from cadconfig.values import freeze
from .schema import ProfileEntry, ProfileValue, SUPPORTED_FLOWS
from cadconfig.scalars import positive_integer_text

class ProfileError(ValueError):
    """A profile failed structural, version, or flow validation."""


@dataclass(frozen=True)
class ProfileDocument:
    """Validated, normalized data ready for a flow-specific SKILL adapter."""

    source: Path
    flow: str
    version: int
    entries: tuple[ProfileEntry, ...]
    raw: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", freeze(self.raw))

    def as_dict(self) -> dict[str, ProfileValue]:
        return dict(self.entries)


def _normalize_flow(flow: str) -> str:
    if not isinstance(flow, str):
        raise ProfileError("Profile flow must be a string")
    normalized = flow.strip().upper()
    if normalized not in SUPPORTED_FLOWS:
        supported = ", ".join(sorted(SUPPORTED_FLOWS))
        raise ProfileError(f"Unsupported profile flow {flow!r}; expected {supported}")
    return normalized


def _control_free(value: str, path: str) -> str:
    multiline = path in {"drc.custom_svrf_command", "lvs.custom_svrf_command"}
    if any(
        (ord(character) < 32 and not (multiline and character in "\n\t"))
        or ord(character) == 127
        for character in value
    ):
        raise ProfileError(f"Control characters are not allowed in {path}")
    return value


def _normalize_value(value: Any, path: str) -> ProfileValue:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _control_free(value, path)
    if isinstance(value, int):
        return value
    if isinstance(value, (list, tuple)):
        return tuple(
            _normalize_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    if isinstance(value, Mapping):
        raise ProfileError(f"Internal error: table found where value expected at {path}")
    raise ProfileError(
        f"Unsupported value type at {path}: {type(value).__name__}; "
        "use a string, boolean, integer, or list"
    )


def _flatten_table(
    table: Mapping[str, Any], prefix: str = ""
) -> Iterator[ProfileEntry]:
    for key in sorted(table):
        if not isinstance(key, str) or not key:
            raise ProfileError(f"Invalid TOML key below {prefix or '<root>'}")
        path = f"{prefix}.{key}" if prefix else key
        value = table[key]
        if isinstance(value, Mapping):
            yield from _flatten_table(value, path)
        else:
            yield path, _normalize_value(value, path)


def _require_table(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = raw.get(name)
    if not isinstance(value, Mapping):
        raise ProfileError(f"TOML section [{name}] is required and must be a table")
    return value


def _optional_text(
    table: Mapping[str, Any], key: str, path: str
) -> str | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{path} must be a non-empty string")
    return _control_free(value.strip(), path)


def _get_path(raw: Mapping[str, Any], path: str) -> Any:
    value: Any = raw
    for component in path.split("."):
        if not isinstance(value, Mapping) or component not in value:
            return None
        value = value[component]
    return value


def _positive_integer_text(value: Any, path: str) -> str:
    try:
        return positive_integer_text(value, path)
    except ValueError as exc:
        raise ProfileError(str(exc)) from exc


def _validate_positive_integer_text(value: Any, path: str) -> None:
    _positive_integer_text(value, path)
