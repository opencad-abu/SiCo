"""MCP operations over the server-owned schematic snapshot store."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .inspection import SCHEMATIC_SNAPSHOT_TOOLS as SNAPSHOT_NAMES
from .schematic_snapshot import SchematicSnapshotStore, SnapshotError, SnapshotNotFound
from .skill_diagnostics import carry_output, record_output


def dispatch_snapshot(name, arguments, *, client, get_store: Callable[[], SchematicSnapshotStore]):
    try:
        detail = _snapshot_tool(name, arguments, client=client, store=get_store())
        return detail.get("ok") is not False, detail
    except (SnapshotError, OSError) as exc:
        return False, {
            "code": "snapshot_not_found" if isinstance(exc, SnapshotNotFound) else "snapshot_error",
            "message": str(exc),
        }


def _snapshot_tool(name, arguments, *, client, store):
    if name == "snapshot_schematic":
        ok, detail = client.call("snapshot_schematic", arguments)
        record_output(detail)
        if not ok:
            return carry_output({"ok": False, **detail}, detail)
        artifact = detail.get("artifact")
        if not isinstance(artifact, Mapping):
            raise SnapshotError("controller did not return a snapshot artifact")
        return carry_output(store.register(artifact), detail)
    if name == "query_schematic":
        _require_snapshot_arguments(
            arguments,
            {
                "snapshot_id",
                "entity",
                "name",
                "name_prefix",
                "hierarchy_path",
                "net",
                "limit",
                "cursor",
            },
            {"snapshot_id"},
        )
        return store.query(
            _snapshot_string(arguments, "snapshot_id"),
            entity=_snapshot_string(arguments, "entity", default="all"),
            name=_snapshot_optional_string(arguments, "name"),
            name_prefix=_snapshot_optional_string(arguments, "name_prefix"),
            hierarchy_path=_snapshot_optional_string(
                arguments, "hierarchy_path", allow_empty=True
            ),
            net=_snapshot_optional_string(arguments, "net"),
            limit=_snapshot_integer(arguments, "limit", default=50),
            cursor=_snapshot_optional_string(arguments, "cursor", allow_empty=True),
        )
    if name == "get_schematic_item":
        _require_snapshot_arguments(
            arguments,
            {"snapshot_id", "entity", "name", "hierarchy_path"},
            {"snapshot_id", "entity", "name"},
        )
        return store.get_item(
            _snapshot_string(arguments, "snapshot_id"),
            entity=_snapshot_string(arguments, "entity"),
            name=_snapshot_string(arguments, "name"),
            hierarchy_path=_snapshot_optional_string(
                arguments, "hierarchy_path", allow_empty=True
            ),
        )
    if name == "export_schematic_text":
        _require_snapshot_arguments(
            arguments,
            {"snapshot_id", "entity", "name_prefix"},
            {"snapshot_id"},
        )
        return store.export_text(
            _snapshot_string(arguments, "snapshot_id"),
            entity=_snapshot_string(arguments, "entity", default="all"),
            name_prefix=_snapshot_optional_string(arguments, "name_prefix"),
        )
    raise SnapshotError(f"unsupported schematic snapshot tool: {name}")


def _require_snapshot_arguments(
    arguments: Mapping[str, Any], allowed: set[str], required: set[str]
) -> None:
    unknown = set(arguments) - allowed
    if unknown:
        raise SnapshotError(f"unsupported snapshot argument(s): {', '.join(sorted(unknown))}")
    missing = required - set(arguments)
    if missing:
        raise SnapshotError(f"snapshot argument is required: {', '.join(sorted(missing))}")


def _snapshot_string(
    arguments: Mapping[str, Any], field: str, *, default: str | None = None
) -> str:
    value = arguments.get(field, default)
    if not isinstance(value, str) or not value:
        raise SnapshotError(f"{field} must be a non-empty string")
    if len(value) > 1024 or any(ord(character) < 32 for character in value):
        raise SnapshotError(f"{field} is invalid")
    return value


def _snapshot_optional_string(
    arguments: Mapping[str, Any], field: str, *, allow_empty: bool = False
) -> str | None:
    if field not in arguments:
        return None
    value = arguments[field]
    if not isinstance(value, str) or (not value and not allow_empty):
        raise SnapshotError(f"{field} must be a string")
    if len(value) > 1024 or any(ord(character) < 32 for character in value):
        raise SnapshotError(f"{field} is invalid")
    return value


def _snapshot_integer(
    arguments: Mapping[str, Any], field: str, *, default: int
) -> int:
    value = arguments.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SnapshotError(f"{field} must be an integer")
    return value
