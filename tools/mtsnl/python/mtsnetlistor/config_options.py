"""Decode models, corner bundles, simulator options and publish targets."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from .errors import RequestValidationError
from .model_entries import ModelEntry, CornerProfile, CornerExport
from .model_options import SimulatorOption
from .model_design import TargetSelection
from .config_schema import MODEL_KEYS, OPTION_KEYS
from .config_values import _table, _strict_keys, _required_text, _config_path, _bool


def _parse_models(raw_models: Any, base: Path, label: str) -> tuple[ModelEntry, ...]:
    if not isinstance(raw_models, list):
        raise RequestValidationError(f"{label} must be an array of tables")
    models: list[ModelEntry] = []
    for index, item in enumerate(raw_models, start=1):
        table = _table(item, f"{label}[{index}]")
        _strict_keys(table, MODEL_KEYS, f"{label}[{index}]")
        models.append(ModelEntry(
            _config_path(table.get("file"), base, f"{label}[{index}].file"),  # type: ignore[arg-type]
            str(table.get("section", "")),
            str(table.get("label", "")),
            _bool(table.get("enabled", True), f"{label}[{index}].enabled"),
        ))
    return tuple(models)


def _parse_corner_export(raw: Any, base: Path, label: str) -> CornerExport:
    table = _table(raw, label)
    _strict_keys(table, {"mode", "variable", "profiles"}, label)
    profiles = table.get("profiles", [])
    if not isinstance(profiles, list):
        raise RequestValidationError(f"{label}.profiles must be an array of tables")
    values = []
    for index, item in enumerate(profiles):
        item_label = f"{label}.profiles[{index}]"
        profile = _table(item, item_label)
        _strict_keys(profile, {"name", "models"}, item_label)
        values.append(CornerProfile(
            _required_text(profile, "name", item_label),
            _parse_models(profile.get("models", []), base, item_label + ".models"),
        ))
    return CornerExport(str(table.get("mode", "fixed")), str(table.get("variable", "mts_corner")), tuple(values))


def _parse_options(raw_options: Any, label: str) -> tuple[SimulatorOption, ...]:
    if not isinstance(raw_options, list):
        raise RequestValidationError(f"{label} must be an array of tables")
    options: list[SimulatorOption] = []
    for index, item in enumerate(raw_options, start=1):
        table = _table(item, f"{label}[{index}]")
        _strict_keys(table, OPTION_KEYS, f"{label}[{index}]")
        enum_values = table.get("enum_values", [])
        if not isinstance(enum_values, list) or not all(isinstance(value, str) for value in enum_values):
            raise RequestValidationError(f"{label}[{index}].enum_values must be an array of strings")
        if "name" not in table or "value" not in table:
            raise RequestValidationError(f"{label}[{index}] requires name and value")
        options.append(SimulatorOption(
            str(table["name"]),
            str(table["value"]),
            str(table.get("value_type", "string")),
            _bool(table.get("enabled", True), f"{label}[{index}].enabled"),
            tuple(enum_values),
        ))
    return tuple(options)


def _target_from_mapping(raw: Mapping[str, Any], label: str) -> TargetSelection:
    return TargetSelection(
        None
        if str(raw.get("target_library", "")).strip() == ""
        else str(raw.get("target_library")),
        None
        if str(raw.get("target_cell", "")).strip() == ""
        else str(raw.get("target_cell")),
        _bool(raw.get("generate_symbol_view", False), f"{label}.generate_symbol_view"),
        _bool(raw.get("generate_netlist_view", False), f"{label}.generate_netlist_view"),
        str(raw.get("overwrite", "reject")),
        _bool(raw.get("overwrite_symbol_view", False), f"{label}.overwrite_symbol_view"),
        _bool(raw.get("overwrite_netlist_view", False), f"{label}.overwrite_netlist_view"),
    )
