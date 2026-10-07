"""Schematic snapshot format, bounds and record validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SNAPSHOT_SCHEMA_VERSION = "cad.schematic.snapshot.v1"
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_SNAPSHOT_LINE_BYTES = 4 * 1024 * 1024
MAX_QUERY_LIMIT = 100


class SnapshotError(ValueError):
    """Raised when a snapshot request or artifact is invalid."""


class SnapshotNotFound(SnapshotError):
    """Raised when a requested session snapshot does not exist."""


def validate_header(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotError("unsupported schematic snapshot schema version")
    if value.get("kind") != "schematic":
        raise SnapshotError("snapshot kind must be schematic")
    if value.get("hierarchy_included") is not False:
        raise SnapshotError("snapshot v1 must declare hierarchy_included=false")
    cellview = value.get("cellview")
    if not isinstance(cellview, dict):
        raise SnapshotError("snapshot header has no cellview identity")
    for key in ("lib", "cell", "view"):
        if not isinstance(cellview.get(key), str) or not cellview[key]:
            raise SnapshotError(f"snapshot cellview.{key} is invalid")
    view_type = value.get("view_type")
    if view_type != "schematic":
        raise SnapshotError("snapshot view_type must be schematic")
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "kind": "schematic",
        "cellview": {key: cellview[key] for key in ("lib", "cell", "view")},
        "view_type": view_type,
        "modified": value.get("modified") if isinstance(value.get("modified"), bool) else None,
        "hierarchy_included": False,
    }


def validate_record(entity: str, data: Mapping[str, Any], line_number: int) -> None:
    if entity == "instance":
        for field in ("lib", "cell", "view"):
            if not isinstance(data.get(field), str) or not data[field]:
                raise SnapshotError(f"snapshot line {line_number} has an invalid instance {field}")
        nonnegative_integer(data, "term_count", line_number)
        terminals = data.get("terminals")
        if not isinstance(terminals, list) or len(terminals) != data["term_count"]:
            raise SnapshotError(
                f"snapshot line {line_number} instance terminal count does not match"
            )
        for terminal in terminals:
            if not isinstance(terminal, dict):
                raise SnapshotError(f"snapshot line {line_number} has an invalid terminal")
            required_string(terminal, "name", line_number)
            nullable_string(terminal, "net", line_number)
    elif entity == "net":
        nonnegative_integer(data, "bits", line_number)
        nonnegative_integer(data, "instance_terminal_count", line_number)
        required_string(data, "signal_type", line_number)
        if not isinstance(data.get("global"), bool):
            raise SnapshotError(f"snapshot line {line_number} has an invalid global flag")
    else:
        required_string(data, "direction", line_number)
        nonnegative_integer(data, "bits", line_number)
        nonnegative_integer(data, "pin_count", line_number)
        pins = data.get("pins")
        if not isinstance(pins, list) or len(pins) != data["pin_count"]:
            raise SnapshotError(f"snapshot line {line_number} terminal pin count does not match")
        for pin in pins:
            if not isinstance(pin, dict):
                raise SnapshotError(f"snapshot line {line_number} has an invalid pin")
            nullable_string(pin, "net", line_number)


def required_string(value: Mapping[str, Any], field: str, line_number: int) -> None:
    if not isinstance(value.get(field), str) or not value[field]:
        raise SnapshotError(f"snapshot line {line_number} has an invalid {field}")


def nullable_string(value: Mapping[str, Any], field: str, line_number: int) -> None:
    item = value.get(field)
    if item is not None and not isinstance(item, str):
        raise SnapshotError(f"snapshot line {line_number} has an invalid {field}")


def nonnegative_integer(value: Mapping[str, Any], field: str, line_number: int) -> None:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
        raise SnapshotError(f"snapshot line {line_number} has an invalid {field}")
