"""Versioned two-column TSV protocol for SKILL-to-Python GUI handoffs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Tuple

from .transfer import atomic_publish_text


PROTOCOL_VERSION = "1"
TransferRecord = Tuple[str, str]


@dataclass(frozen=True)
class TransferDocument:
    records: tuple[TransferRecord, ...] = ()
    status: str | None = None
    version: str | None = None


def _validate_field(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"Transfer {field} must be a string")
    text = value
    if not text or any(character in text for character in "\t\r\n"):
        raise ValueError(f"Transfer {field} must be non-empty single-line text")
    return text


def read_transfer(path: str | Path | None) -> TransferDocument:
    """Read metadata and ordered records from a two-column TSV document."""
    if path is None:
        return TransferDocument()
    source = Path(path).expanduser()
    records: list[TransferRecord] = []
    status: str | None = None
    version: str | None = None
    with source.open("r", encoding="utf-8") as stream:
        for line_number, raw_line in enumerate(stream, 1):
            line = raw_line.rstrip("\r\n")
            if not line or (line.startswith("#") and "\t" not in line):
                continue
            fields = line.split("\t")
            if len(fields) != 2:
                raise ValueError(
                    f"Malformed transfer input on line {line_number}: "
                    "expected a two-column TSV record"
                )
            kind = _validate_field(fields[0], "record kind")
            value = _validate_field(fields[1], "record value")
            if kind == "#status":
                if status is not None:
                    raise ValueError("Duplicate transfer status record")
                status = value
            elif kind == "#version":
                if version is not None:
                    raise ValueError("Duplicate transfer version record")
                version = value
            elif kind.startswith("#"):
                continue
            else:
                records.append((kind, value))
    return TransferDocument(tuple(records), status, version)


def write_transfer(
    path: str | Path,
    records: Iterable[TransferRecord],
    *,
    status: str | None = None,
    version: str | None = None,
    refuse_existing: bool = False,
) -> Path:
    """Atomically publish metadata and ordered two-column TSV records."""
    lines: list[str] = []
    if version is not None:
        lines.append(f"#version\t{_validate_field(version, 'version')}\n")
    if status is not None:
        lines.append(f"#status\t{_validate_field(status, 'status')}\n")
    for kind, value in records:
        lines.append(
            f"{_validate_field(kind, 'record kind')}\t"
            f"{_validate_field(value, 'record value')}\n"
        )
    return atomic_publish_text(path, "".join(lines), refuse_existing=refuse_existing)
