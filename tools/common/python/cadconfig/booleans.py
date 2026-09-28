"""Shared boolean field vocabulary, defaults and typed validation."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

from .values import MISSING, lookup


_LVS = frozenset("""
input.cdl_include_enable lvs.hcell_enable lvs.ignore_error lvs.case_sensitive
lvs.custom_svrf_enable lvs.virtual_connect_enable lvs.virtual_connect_name_enable
""".split())
FLOW_BOOLEAN_PATHS = MappingProxyType({
    "DRC": frozenset({"drc.rule_select_enable", "drc.custom_svrf_enable"}),
    "LVS": _LVS,
    "RCE": _LVS | frozenset("""
        extract.start_rve reduction.enabled reduction.reduce_negative
        selection.net_enable selection.cell_enable filter.cap_percentage_enable
        filter.cap_value_enable filter.res_value_enable netlist.create_view
        netlist.pin_order_enable netlist.brackets_replace
        netlist.hierarchy_delimiter_enable netlist.parasitic_coordinates
        netlist.parasitic_res_layer netlist.parasitic_res_dimensions
    """.split()),
    "LEF": frozenset("""
        steps.pins steps.extract steps.abstract output.geometry output.technology
    """.split()),
})
BOOLEAN_DEFAULTS = MappingProxyType({
    path: path in {"lvs.case_sensitive", "steps.pins", "steps.extract",
                   "steps.abstract", "output.geometry"}
    for paths in FLOW_BOOLEAN_PATHS.values() for path in paths
})
REQUEST_BOOLEAN_PATHS = frozenset(BOOLEAN_DEFAULTS) | frozenset({
    "options.replace_bus_bit_char", "extract.starrc.three_d_ic",
})


def strict_bool(value: Any, field: str) -> bool:
    if type(value) is bool:
        return value
    raise ValueError(f"{field} must be true or false")


def validate_booleans(raw: Mapping[str, Any], paths: frozenset[str]) -> None:
    for path in sorted(paths):
        value = lookup(raw, path)
        if value is not MISSING:
            strict_bool(value, path)


def boolean_defaults(flow: str) -> dict[str, bool]:
    return {path: BOOLEAN_DEFAULTS[path] for path in FLOW_BOOLEAN_PATHS[flow]}


def boolean_value(raw: Mapping[str, Any], path: str, default: Any = MISSING) -> bool:
    value = lookup(raw, path)
    if value is MISSING:
        value = BOOLEAN_DEFAULTS.get(path, False) if default is MISSING else default
    return strict_bool(value, path)
