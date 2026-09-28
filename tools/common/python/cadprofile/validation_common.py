"""Structural and cross-flow profile validation."""

from __future__ import annotations

from typing import Any, Mapping

from cadconfig.booleans import validate_booleans
from cadconfig.scalars import validate_scalars
from .schema import (
    _ARRAY_PATHS, _ALLOWED_TOP_LEVEL, _FLOW_ALLOWED_PATHS, _FLOW_BOOLEAN_PATHS, _FLOW_DYNAMIC_PATH_PREFIXES,
    _FLOW_ENUM_VALUES, _GUI_TEXT_PATHS, _INTEGER_TEXT_PATHS, _INPUT_TYPES,
    _METADATA_KEYS, _REQUIRED_TABLES, FORMAT_NAME, SCHEMA_VERSION,
    _BIN_OPTION_NAME,
)
from .values import (
    ProfileError, _flatten_table, _get_path, _normalize_flow, _require_table,
    _validate_positive_integer_text,
)

def _validate_metadata(
    raw: Mapping[str, Any], expected_flow: str
) -> int:
    metadata = raw.get("cad_config")
    if metadata is None:
        raise ProfileError(
            "TOML section [cad_config] is required; execution TOML is not "
            "accepted as a configuration profile"
        )
    if not isinstance(metadata, Mapping):
        raise ProfileError("TOML section [cad_config] must be a table")
    unknown = sorted(set(metadata) - _METADATA_KEYS)
    if unknown:
        raise ProfileError(
            "Unknown [cad_config] option(s): " + ", ".join(unknown)
        )
    format_name = metadata.get("format")
    if format_name != FORMAT_NAME:
        raise ProfileError(
            f"Unsupported cad_config.format {format_name!r}; expected {FORMAT_NAME!r}"
        )
    version = metadata.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ProfileError("cad_config.version must be an integer")
    if version != SCHEMA_VERSION:
        raise ProfileError(
            f"Unsupported profile schema version {version}; expected {SCHEMA_VERSION}"
        )
    configured_flow = _normalize_flow(metadata.get("flow", ""))
    if configured_flow != expected_flow:
        raise ProfileError(
            f"Profile flow is {configured_flow}, not requested flow {expected_flow}"
        )
    return version


def _validate_top_level(raw: Mapping[str, Any], flow: str) -> None:
    unknown = sorted(set(raw) - _ALLOWED_TOP_LEVEL[flow])
    if unknown:
        raise ProfileError(
            f"Unknown top-level section(s) for {flow}: " + ", ".join(unknown)
        )
    missing = sorted(_REQUIRED_TABLES[flow] - set(raw))
    if missing:
        raise ProfileError(
            f"Missing required section(s) for {flow}: " + ", ".join(missing)
        )
    for name in set(raw) - {"cad_config"}:
        if not isinstance(raw[name], Mapping):
            raise ProfileError(f"TOML section [{name}] must be a table")


def _validate_profile_paths(raw: Mapping[str, Any], flow: str) -> None:
    allowed = _FLOW_ALLOWED_PATHS[flow]
    prefixes = _FLOW_DYNAMIC_PATH_PREFIXES[flow]
    for path, _value in _flatten_table(
        {key: value for key, value in raw.items() if key != "cad_config"}
    ):
        if path in allowed:
            continue
        if any(path.startswith(prefix) for prefix in prefixes):
            suffix = next(
                path.removeprefix(prefix)
                for prefix in prefixes
                if path.startswith(prefix)
            )
            if suffix and _BIN_OPTION_NAME.fullmatch(suffix):
                continue
        raise ProfileError(f"Unknown profile option for {flow}: {path}")


def _validate_field_types(raw: Mapping[str, Any], flow: str) -> None:
    try:
        validate_booleans(raw, _FLOW_BOOLEAN_PATHS[flow])
        validate_scalars(raw)
    except ValueError as exc:
        raise ProfileError(str(exc)) from exc

    for path in _ARRAY_PATHS & _FLOW_ALLOWED_PATHS[flow]:
        value = _get_path(raw, path)
        if value is not None and not isinstance(value, list):
            raise ProfileError(f"{path} must be an array")

    for path in _GUI_TEXT_PATHS[flow]:
        value = _get_path(raw, path)
        if value is not None and not isinstance(value, str):
            raise ProfileError(f"{path} must be a string")

    for path, allowed in _FLOW_ENUM_VALUES[flow].items():
        value = _get_path(raw, path)
        if value is not None and value not in allowed:
            expected = ", ".join(repr(item) for item in sorted(allowed))
            raise ProfileError(f"{path} must be one of {expected}")

    if flow == "LEF":
        for path, value in _flatten_table(raw.get("abstract", {}), "abstract"):
            if path.startswith("abstract.bin_options.") and not isinstance(
                value, (str, bool, int)
            ):
                raise ProfileError(
                    f"{path} must be a string, boolean, or integer"
                )

    for path in _INTEGER_TEXT_PATHS:
        value = _get_path(raw, path)
        if value is not None:
            _validate_positive_integer_text(value, path)

    _validate_profile_paths(raw, flow)


def _validate_common(raw: Mapping[str, Any], flow: str) -> None:
    run = _require_table(raw, "run")
    run_type = run.get("run_type", "Current Host")
    if run_type not in {"Current Host", "LSF Farm"}:
        raise ProfileError(
            "run.run_type must be 'Current Host' or 'LSF Farm'"
        )
    if "cpus" in run:
        _validate_positive_integer_text(run["cpus"], "run.cpus")

    if flow != "LEF":
        inp = _require_table(raw, "input")
        input_type = inp.get("type", "OA")
        if not isinstance(input_type, str):
            raise ProfileError("input.type must be a string")
        if input_type not in _INPUT_TYPES[flow]:
            expected = ", ".join(sorted(_INPUT_TYPES[flow]))
            raise ProfileError(
                f"Unsupported input.type {input_type!r} for {flow}; expected {expected}"
            )

    runtime = raw.get("runtime", {})
    if isinstance(runtime, Mapping):
        for key in ("cpus", "lvs_cpus", "ext_cpus"):
            if key in runtime:
                _validate_positive_integer_text(runtime[key], f"runtime.{key}")

    batch = raw.get("batch")
    if batch is not None:
        scope = batch.get("scope", "Single Cell")
        if scope not in {"Single Cell", "Multiple Cells"}:
            raise ProfileError(
                "batch.scope must be 'Single Cell' or 'Multiple Cells'"
            )
        if "parallel_cells" in batch:
            _validate_positive_integer_text(
                batch["parallel_cells"], "batch.parallel_cells"
            )
        tasks = batch.get("tasks", [])
        if not isinstance(tasks, list):
            raise ProfileError("batch.tasks must be an array of arrays")
        width = 6 if flow in {"LVS", "RCE"} else 3
        for index, row in enumerate(tasks):
            if not isinstance(row, list) or len(row) != width:
                raise ProfileError(
                    f"batch.tasks[{index}] must contain exactly {width} strings"
                )
            for column, value in enumerate(row):
                if not isinstance(value, str):
                    raise ProfileError(
                        f"batch.tasks[{index}][{column}] must be a string"
                    )
