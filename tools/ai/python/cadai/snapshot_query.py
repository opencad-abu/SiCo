"""Snapshot query value validation, cursors and text projection."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable, Mapping
from typing import Any

from .snapshot_schema import MAX_QUERY_LIMIT, SnapshotError


def validate_entity(entity: str, *, allow_all: bool = True) -> None:
    allowed = {"instance", "net", "terminal"}
    if allow_all:
        allowed.add("all")
    if entity not in allowed:
        raise SnapshotError("entity must be one of instance, net, terminal, or all")


def validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_QUERY_LIMIT:
        raise SnapshotError(f"limit must be an integer between 1 and {MAX_QUERY_LIMIT}")


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if cursor is None or cursor == "":
        return 0
    if not isinstance(cursor, str) or len(cursor) > 32:
        raise SnapshotError("cursor is invalid")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = int(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise SnapshotError("cursor is invalid") from exc
    if value < 0 or value > 2**63 - 1 - MAX_QUERY_LIMIT:
        raise SnapshotError("cursor is invalid")
    return value


def like_prefix(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise SnapshotError("name_prefix must be a non-empty string")
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def text_chunks(
    summary: Mapping[str, Any], rows: Iterable[tuple[str, str, str, str]]
) -> Iterable[bytes]:
    summary = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    yield ("# sico-ai schematic snapshot\n# summary " + summary + "\n").encode("utf-8")
    for entity, name, path, payload in rows:
        prefix = f"{entity}\t{name}"
        if path:
            prefix += f"\t{path}"
        yield (prefix + "\t" + payload + "\n").encode("utf-8")
