"""Build a SQLite index from a validated schematic JSONL stream."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .snapshot_schema import (
    MAX_SNAPSHOT_LINE_BYTES,
    SnapshotError,
    validate_header,
    validate_record,
)


def create_schema(connection: Any) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=FULL;
        CREATE TABLE records (
            ordinal INTEGER PRIMARY KEY,
            entity TEXT NOT NULL,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE INDEX records_entity_name ON records(entity, name);
        CREATE INDEX records_path ON records(path);
        CREATE TABLE connections (
            ordinal INTEGER NOT NULL,
            net TEXT NOT NULL,
            pin_name TEXT NOT NULL,
            FOREIGN KEY(ordinal) REFERENCES records(ordinal)
        );
        CREATE INDEX connections_net ON connections(net, ordinal);
        """
    )


def index_jsonl(path: Path, connection: Any) -> dict[str, Any]:
    header: dict[str, Any] | None = None
    footer: dict[str, Any] | None = None
    counts = {"instances": 0, "nets": 0, "terminals": 0}
    ordinal = 0
    saw_footer = False
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SnapshotError("snapshot artifact cannot be opened safely") from exc
    with os.fdopen(descriptor, "rb") as raw_handle:
        for line_number, raw_line in enumerate(raw_handle, 1):
            if len(raw_line) > MAX_SNAPSHOT_LINE_BYTES:
                raise SnapshotError(f"snapshot line {line_number} exceeds the size limit")
            try:
                line = raw_line.decode("utf-8")
                value = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SnapshotError(f"snapshot line {line_number} is invalid JSON") from exc
            if not isinstance(value, dict) or not isinstance(value.get("record_type"), str):
                raise SnapshotError(f"snapshot line {line_number} is not a typed record")
            record_type = value["record_type"]
            if line_number == 1:
                if record_type != "header":
                    raise SnapshotError("snapshot must start with a header")
                header = validate_header(value)
                continue
            if record_type == "footer":
                if saw_footer:
                    raise SnapshotError("snapshot contains more than one footer")
                footer = value
                saw_footer = True
                continue
            if saw_footer:
                raise SnapshotError("snapshot contains records after its footer")
            if record_type not in {"instance", "net", "terminal"}:
                raise SnapshotError(f"unsupported snapshot record type: {record_type}")
            data = value.get("data")
            if not isinstance(data, dict):
                raise SnapshotError(f"snapshot line {line_number} has no object data")
            name = data.get("name")
            if not isinstance(name, str) or not name:
                raise SnapshotError(f"snapshot line {line_number} has an invalid name")
            path_value = data.get("hierarchy_path", "")
            if "hierarchy_path" not in data:
                data["hierarchy_path"] = ""
            if not isinstance(path_value, str):
                raise SnapshotError(f"snapshot line {line_number} has an invalid hierarchy_path")
            entity = record_type
            validate_record(entity, data, line_number)
            ordinal += 1
            payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            connection.execute(
                "INSERT INTO records(ordinal, entity, name, path, payload) VALUES (?, ?, ?, ?, ?)",
                (ordinal, entity, name, path_value, payload),
            )
            counts[{"instance": "instances", "net": "nets", "terminal": "terminals"}[entity]] += 1
            index_connections(connection, ordinal, entity, data)
    if header is None:
        raise SnapshotError("snapshot is empty")
    if footer is None:
        raise SnapshotError("snapshot is missing its footer")
    footer_counts = footer.get("counts")
    if footer.get("truncated") is not False:
        raise SnapshotError("snapshot footer must explicitly declare truncated=false")
    if not isinstance(footer_counts, dict) or any(
        footer_counts.get(key) != value for key, value in counts.items()
    ):
        raise SnapshotError("snapshot footer counts do not match its records")
    return {
        **header,
        "counts": counts,
        "complete": True,
        "hierarchy_included": bool(header.get("hierarchy_included", False)),
    }


def index_connections(connection: Any, ordinal: int, entity: str, data: Mapping[str, Any]) -> None:
    if entity == "instance":
        terminals = data.get("terminals", [])
        if not isinstance(terminals, list):
            raise SnapshotError("instance terminals must be a list")
        for terminal in terminals:
            if not isinstance(terminal, dict):
                raise SnapshotError("instance terminal entry is invalid")
            net = terminal.get("net")
            pin_name = terminal.get("name", "")
            if isinstance(net, str) and net and isinstance(pin_name, str):
                connection.execute(
                    "INSERT INTO connections(ordinal, net, pin_name) VALUES (?, ?, ?)",
                    (ordinal, net, pin_name),
                )
    elif entity == "terminal":
        pins = data.get("pins", [])
        if not isinstance(pins, list):
            raise SnapshotError("terminal pins must be a list")
        for pin in pins:
            if not isinstance(pin, dict):
                raise SnapshotError("terminal pin entry is invalid")
            net = pin.get("net")
            if isinstance(net, str) and net:
                connection.execute(
                    "INSERT INTO connections(ordinal, net, pin_name) VALUES (?, ?, ?)",
                    (ordinal, net, ""),
                )
