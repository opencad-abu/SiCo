"""Explicit migration adapter for historical execution boolean encodings.

This module is intentionally opt-in. It is the only place where pre-profile
SKILL-like boolean words and the positional ``addon_info`` field are accepted.
Remove this adapter and --legacy-config once all site execution templates use
typed booleans and named parasitic flags, with no conversions in an acceptance run.
"""

from __future__ import annotations

from typing import Any, Mapping
import warnings

from cadconfig.booleans import REQUEST_BOOLEAN_PATHS
from cadconfig.values import MISSING, lookup, thaw
from .config_input import validate_execution_root


class LegacyConfigWarning(UserWarning):
    """A historical field was converted at the legacy input boundary."""


def _legacy_bool(value: Any, field: str) -> bool:
    if type(value) is bool:
        return value
    if type(value) in (int, float) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().strip('"').casefold()
        if word in {"1", "t", "true", "yes", "y", "on"}:
            return True
        if word in {"0", "nil", "false", "no", "n", "off", ""}:
            return False
    raise ValueError(f"Invalid legacy boolean value for {field}: {value!r}")


def adapt_legacy(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached typed input; unknown values always fail conversion."""
    validate_execution_root(raw)
    result = thaw(raw)
    for path in sorted(REQUEST_BOOLEAN_PATHS):
        value = lookup(result, path)
        if value is MISSING or type(value) is bool:
            continue
        node = result
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = _legacy_bool(value, path)
        warnings.warn(
            f"Legacy configuration converted {path}; write typed TOML booleans",
            LegacyConfigWarning, stacklevel=2,
        )
    netlist = result.get("netlist", {})
    remove_instances = netlist.get("dspf_remove_instances")
    if type(remove_instances) is bool:
        netlist["dspf_remove_instances"] = "TRUE" if remove_instances else "FALSE"
        warnings.warn(
            "Legacy configuration converted netlist.dspf_remove_instances; use TRUE/FALSE strings",
            LegacyConfigWarning, stacklevel=2,
        )
    if "addon_info" in netlist:
        value = netlist.pop("addon_info")
        if isinstance(value, str):
            value = value.strip().strip("()").replace('"', "").replace(",", " ").split()
        if not isinstance(value, (list, tuple)) or len(value) > 3:
            raise ValueError("netlist.addon_info accepts exactly three boolean positions")
        flags = [_legacy_bool(item, f"netlist.addon_info[{index}]")
                 for index, item in enumerate(value)]
        flags += [False] * (3 - len(flags))
        for name, enabled in zip(
            ("parasitic_coordinates", "parasitic_res_layer", "parasitic_res_dimensions"), flags
        ):
            netlist.setdefault(name, enabled)
        warnings.warn(
            "Legacy configuration converted netlist.addon_info; use named fields",
            LegacyConfigWarning, stacklevel=2,
        )
    return result
