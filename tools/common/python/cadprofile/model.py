"""Public profile loader facade with compatibility exports."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .compat import tomllib
from cadconfig.paths import PATH_KEYS as _PATH_KEYS
from cadconfig.paths import SELECTION_PATH_KEYS as _SELECTION_FILE_OR_INLINE_KEYS
from .schema import (
    FORMAT_NAME, SCHEMA_VERSION, SUPPORTED_FLOWS,
    ProfileScalar, ProfileValue, ProfileEntry,
    _METADATA_KEYS, _ALLOWED_TOP_LEVEL, _REQUIRED_TABLES, _INPUT_TYPES,
    _FLOW_ALLOWED_PATHS, _FLOW_DYNAMIC_PATH_PREFIXES, _ARRAY_PATHS,
    _GUI_TEXT_PATHS, _FLOW_ENUM_VALUES, _BIN_OPTION_NAME, _REDUCTION_OUTPUT_TAG,
    _FLOW_BOOLEAN_PATHS,
    _INTEGER_TEXT_PATHS,
)
from .values import (
    ProfileError, ProfileDocument,
    _normalize_flow, _control_free, _normalize_value, _flatten_table,
    _require_table, _optional_text, _get_path, _positive_integer_text,
    _validate_positive_integer_text,
)
from .validation_common import (
    _validate_metadata, _validate_top_level, _validate_profile_paths,
    _validate_field_types, _validate_common,
)
from .validation_flows import (
    _decimal_text, _validate_run_mode,
    _validate_drc, _validate_lvs, _validate_rce, _validate_lef,
)
from .normalization import (
    _profile_defaults, _normalization_overrides, _normalized_raw, _path_views,
    _set_path,
)

# ``model`` was the original profile module. Keep its established import
# surface as direct aliases while each implementation now lives in its owner.
__all__ = [
    "FORMAT_NAME", "SCHEMA_VERSION", "SUPPORTED_FLOWS", "ProfileScalar",
    "ProfileValue", "ProfileEntry", "ProfileError", "ProfileDocument",
    "load_profile", "path_keys", "_METADATA_KEYS", "_ALLOWED_TOP_LEVEL",
    "_REQUIRED_TABLES", "_INPUT_TYPES", "_FLOW_ALLOWED_PATHS",
    "_FLOW_DYNAMIC_PATH_PREFIXES", "_ARRAY_PATHS", "_GUI_TEXT_PATHS",
    "_FLOW_ENUM_VALUES", "_BIN_OPTION_NAME", "_REDUCTION_OUTPUT_TAG",
    "_FLOW_BOOLEAN_PATHS", "_INTEGER_TEXT_PATHS", "_PATH_KEYS",
    "_SELECTION_FILE_OR_INLINE_KEYS", "_normalize_flow", "_control_free",
    "_normalize_value", "_flatten_table", "_require_table", "_optional_text",
    "_get_path", "_positive_integer_text", "_validate_positive_integer_text",
    "_validate_metadata", "_validate_top_level", "_validate_profile_paths",
    "_validate_field_types", "_validate_common", "_decimal_text",
    "_validate_run_mode", "_validate_drc", "_validate_lvs", "_validate_rce",
    "_validate_lef", "_profile_defaults", "_normalization_overrides",
    "_set_path", "_normalized_raw", "_path_views",
]


def load_profile(path: str | Path, flow: str) -> ProfileDocument:
    """Load a versioned v1 profile for an explicit flow."""

    expected_flow = _normalize_flow(flow)
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Cannot access profile TOML: {source}")
    try:
        with source.open("rb") as stream:
            raw = tomllib.load(stream)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"Invalid profile TOML: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ProfileError("Profile TOML root must be a table")

    version = _validate_metadata(raw, expected_flow)
    _validate_top_level(raw, expected_flow)
    _validate_field_types(raw, expected_flow)
    _validate_common(raw, expected_flow)
    validators = {
        "DRC": _validate_drc,
        "LVS": _validate_lvs,
        "RCE": _validate_rce,
        "LEF": _validate_lef,
    }
    validators[expected_flow](raw)

    body = {key: value for key, value in raw.items() if key != "cad_config"}
    entries = _profile_defaults(expected_flow)
    entries.update(_flatten_table(body))
    normalization = _normalization_overrides(raw, expected_flow, source)
    entries.update(normalization)
    entries.update(_path_views(entries, source, expected_flow))
    return ProfileDocument(
        source=source,
        flow=expected_flow,
        version=version,
        entries=tuple(sorted(entries.items())),
        raw=_normalized_raw(raw, normalization),
    )


def path_keys() -> frozenset[str]:
    """Expose the shared path vocabulary for flow adapters and documentation."""

    return _PATH_KEYS | _SELECTION_FILE_OR_INLINE_KEYS
