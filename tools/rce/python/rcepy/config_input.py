"""Execution TOML adapter feeding the common typed field contract."""

from __future__ import annotations

from typing import Any, Mapping

from cadconfig.booleans import REQUEST_BOOLEAN_PATHS, validate_booleans
from cadconfig.scalars import INTEGER_TEXT_PATHS, positive_integer_text, validate_scalars
from cadconfig.values import MISSING, lookup, thaw


def validate_execution_root(raw: Mapping[str, Any]) -> None:
    if not isinstance(raw, Mapping):
        raise ValueError("Execution TOML root must be a table")
    if "cad_config" in raw:
        raise ValueError("cad_config identifies a profile; export an execution request first")
    for name in (
        "run", "input", "runtime", "batch", "drc", "lvs", "extract", "reduction",
        "selection", "filter", "netlist", "options", "steps", "output", "abstract",
    ):
        if name in raw and not isinstance(raw[name], Mapping):
            raise ValueError(f"Execution TOML section {name} must be a table")


def execution_values(raw: Mapping[str, Any]) -> dict[str, Any]:
    validate_execution_root(raw)
    validate_booleans(raw, REQUEST_BOOLEAN_PATHS)
    validate_scalars(raw)
    if lookup(raw, "netlist.addon_info") is not MISSING:
        raise ValueError("netlist.addon_info requires the explicit legacy configuration adapter")
    result = thaw(raw)
    for path in INTEGER_TEXT_PATHS:
        value = lookup(raw, path)
        if value is not MISSING:
            table, key = path.split(".")
            result[table][key] = positive_integer_text(value, path)
    value = lookup(raw, "netlist.dspf_remove_instances")
    if value is not MISSING and value not in ("TRUE", "FALSE"):
        raise ValueError("netlist.dspf_remove_instances must be 'TRUE' or 'FALSE'")
    return result
