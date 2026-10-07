"""Bounded builders for the read-only Virtuoso inspection MCP tools."""

from __future__ import annotations

from typing import Any

MAX_INSPECT_ITEMS = 200
DEFAULT_INSPECT_ITEMS = 50

READ_ONLY_INSPECTION_TOOLS = frozenset(
    {
        "inspect_schematic",
        "inspect_layout",
        "inspect_symbol_ports",
        "list_libraries",
        "inspect_library",
        "list_windows",
        "inspect_config_binding",
    }
)

SCHEMATIC_SNAPSHOT_TOOLS = frozenset(
    {"snapshot_schematic", "query_schematic", "get_schematic_item", "export_schematic_text"}
)


class InspectionArgumentError(ValueError):
    """Raised when a read-only inspection call cannot be represented safely."""


def build_read_only_skill(name: str, arguments: dict[str, Any]) -> str:
    """Build one fixed-shape SKILL expression for an inspection MCP tool."""
    if name == "inspect_schematic":
        target = _target(arguments, "schematic", "schematic", allow_placement=True)
        placement = _boolean(arguments, "include_placement", default=False)
        return "aiInspectSchematic({} {} {} {} {} {})".format(
            *target, _max_items(arguments), "t" if placement else "nil"
        )
    if name == "inspect_layout":
        target = _target(arguments, "layout", "maskLayout")
        return "aiInspectLayout({} {} {} {} {})".format(*target, _max_items(arguments))
    if name == "inspect_symbol_ports":
        target = _target(arguments, "symbol", "schematicSymbol")
        return "aiInspectSymbolPorts({} {} {} {} {})".format(*target, _max_items(arguments))
    if name == "list_libraries":
        _require_only(arguments, {"max_items"})
        return f"aiListLibraries({_max_items(arguments)})"
    if name == "inspect_library":
        _require_only(arguments, {"name"})
        return f"aiInspectLibrary({_string(arguments, 'name')})"
    if name == "inspect_config_binding":
        _require_only(arguments, {"lib", "cell", "view", "max_items"})
        for key in ("lib", "cell", "view"):
            _string(arguments, key)
        return "aiInspectConfigBinding({} {} {} {})".format(
            _string(arguments, "lib"),
            _string(arguments, "cell"),
            _string(arguments, "view"),
            _max_items(arguments),
        )
    if name == "list_windows":
        _require_only(arguments, {"max_items"})
        return f"aiListWindows({_max_items(arguments)})"
    raise InspectionArgumentError(f"unsupported read-only inspection tool: {name}")


def build_config_binding_skill(arguments: dict[str, Any]) -> str:
    """Build the fixed-shape controller-owned HDB readback call."""
    _require_only(arguments, {"lib", "cell", "view", "max_items"})
    return "aiInspectConfigBinding({} {} {} {})".format(
        _string(arguments, "lib"),
        _string(arguments, "cell"),
        _string(arguments, "view"),
        _max_items(arguments),
    )


def build_snapshot_request(arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate the public snapshot target envelope.

    The controller supplies the private output path when it materializes the
    request.  Keeping that path out of MCP arguments prevents an agent from
    directing Virtuoso writes outside the session spool.
    """
    allowed = {"lib", "cell", "view", "view_type", "include_placement"}
    _require_only(arguments, allowed)
    lib = _optional_string(arguments, "lib")
    cell = _optional_string(arguments, "cell")
    if (lib is None) != (cell is None):
        raise InspectionArgumentError("lib and cell must be supplied together")
    view = _optional_string(arguments, "view")
    view_type = _optional_string(arguments, "view_type")
    if lib is None and (view is not None or view_type is not None):
        raise InspectionArgumentError("view and view_type require lib and cell")
    if view_type is not None and view_type != "schematic":
        raise InspectionArgumentError("snapshot_schematic requires view_type schematic")
    placement = _boolean(arguments, "include_placement", default=False)
    return {
        "lib": lib,
        "cell": cell,
        "view": view or "schematic",
        "view_type": "schematic",
        "include_placement": placement,
    }


def build_snapshot_skill(arguments: dict[str, Any], output_path: str) -> str:
    """Build the fixed-shape SKILL call used by ``snapshot_schematic``."""
    target = build_snapshot_request(arguments)
    if not isinstance(output_path, str) or not output_path:
        raise InspectionArgumentError("snapshot output path is invalid")
    return "aiSnapshotSchematic({} {} {} {} {} {})".format(
        _skill_string(target["lib"]) if target["lib"] is not None else "nil",
        _skill_string(target["cell"]) if target["cell"] is not None else "nil",
        _skill_string(target["view"]),
        _skill_string(target["view_type"]),
        _skill_string(output_path),
        "t" if target["include_placement"] else "nil",
    )


def decode_read_only_result(detail: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Compatibility entry; remove after external inspection callers migrate."""
    from .skill_result import decode_skill_result

    return decode_skill_result(detail)


def _target(
    arguments: dict[str, Any],
    default_view: str,
    default_view_type: str,
    *,
    allow_placement: bool = False,
) -> tuple[str, ...]:
    allowed = {"lib", "cell", "view", "view_type", "max_items"}
    if allow_placement:
        allowed.add("include_placement")
    _require_only(arguments, allowed)
    lib = _optional_string(arguments, "lib")
    cell = _optional_string(arguments, "cell")
    view = _optional_string(arguments, "view")
    view_type = _optional_string(arguments, "view_type")
    if (lib is None) != (cell is None):
        raise InspectionArgumentError("lib and cell must be supplied together")
    if lib is None:
        if view is not None or view_type is not None:
            raise InspectionArgumentError("view and view_type require lib and cell")
        return ("nil", "nil", "nil", "nil")
    return (
        _skill_string(lib),
        _skill_string(cell),
        _skill_string(view or default_view),
        _skill_string(view_type or default_view_type),
    )


def _max_items(arguments: dict[str, Any]) -> int:
    value = arguments.get("max_items", DEFAULT_INSPECT_ITEMS)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InspectionArgumentError("max_items must be an integer")
    if not 1 <= value <= MAX_INSPECT_ITEMS:
        raise InspectionArgumentError(f"max_items must be between 1 and {MAX_INSPECT_ITEMS}")
    return value


def _boolean(arguments: dict[str, Any], field: str, *, default: bool) -> bool:
    value = arguments.get(field, default)
    if not isinstance(value, bool):
        raise InspectionArgumentError(f"{field} must be a boolean")
    return value


def _string(arguments: dict[str, Any], field: str) -> str:
    value = _optional_string(arguments, field)
    if value is None:
        raise InspectionArgumentError(f"{field} is required")
    return _skill_string(value)


def _optional_string(arguments: dict[str, Any], field: str) -> str | None:
    if field not in arguments:
        return None
    value = arguments[field]
    if not isinstance(value, str):
        raise InspectionArgumentError(f"{field} must be a string")
    if not value or value != value.strip():
        raise InspectionArgumentError(f"{field} must be a non-empty trimmed string")
    if len(value) > 256:
        raise InspectionArgumentError(f"{field} exceeds 256 characters")
    if any(ord(character) < 32 for character in value):
        raise InspectionArgumentError(f"{field} contains a control character")
    return value


def _skill_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _require_only(arguments: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(arguments) - allowed
    if unknown:
        raise InspectionArgumentError(f"unsupported argument(s): {', '.join(sorted(unknown))}")


__all__ = [
    "DEFAULT_INSPECT_ITEMS",
    "MAX_INSPECT_ITEMS",
    "READ_ONLY_INSPECTION_TOOLS",
    "SCHEMATIC_SNAPSHOT_TOOLS",
    "InspectionArgumentError",
    "build_snapshot_request",
    "build_snapshot_skill",
    "build_read_only_skill",
    "build_config_binding_skill",
    "decode_read_only_result",
]
