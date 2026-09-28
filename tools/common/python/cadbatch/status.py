"""Atomic JSON/TSV status persistence for CAD multi-cell batches."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .manifest import BatchManifest


TERMINAL_STATES = {
    "succeeded", "succeeded_with_warnings", "failed", "canceled", "awaiting_publication"
}
COUNTED_STATES = (
    "pending",
    "running",
    "awaiting_publication",
    "succeeded",
    "succeeded_with_warnings",
    "failed",
    "canceled",
)
TSV_COLUMNS = (
    "index",
    "id",
    "label",
    "status",
    "exit_code",
    "run_dir",
    "config",
    "launch_log",
    "started_at",
    "finished_at",
    "duration_seconds",
    "result_path",
    "publication_status",
    "publication_message",
    "warning_message",
)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def counts(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        status: sum(record["status"] == status for record in records)
        for status in COUNTED_STATES
    }


def overall_status(
    records: list[dict[str, Any]], *, cancel_requested: bool = False
) -> str:
    totals = counts(records)
    if totals["running"] or totals["pending"]:
        return "canceling" if cancel_requested else "running"
    if totals["awaiting_publication"]:
        return "awaiting_publication"
    if totals["canceled"]:
        return "canceled"
    if totals["failed"]:
        return "completed_with_errors"
    if totals["succeeded_with_warnings"]:
        return "completed_with_warnings"
    return "completed"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _tsv_value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def write_status(manifest: BatchManifest, payload: dict[str, Any]) -> None:
    _atomic_write(
        manifest.status_json,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )
    lines = ["\t".join(TSV_COLUMNS)]
    for record in payload["tasks"]:
        lines.append(
            "\t".join(_tsv_value(record.get(column)) for column in TSV_COLUMNS)
        )
    _atomic_write(manifest.status_tsv, "\n".join(lines) + "\n")


def finalize_publications(manifest: BatchManifest, path: str | Path) -> int:
    publication_path = Path(path).expanduser().resolve()
    payload = json.loads(manifest.status_json.read_text(encoding="utf-8"))
    if payload.get("batch_id") != manifest.batch_id:
        raise ValueError("Status file does not belong to this batch manifest")
    with publication_path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if reader.fieldnames is None or not {"id", "status"}.issubset(
            reader.fieldnames
        ):
            raise ValueError("Publication TSV requires id and status columns")
        publications = {str(row["id"]): row for row in reader}
    for record in payload["tasks"]:
        if record["status"] != "awaiting_publication":
            continue
        result = publications.get(str(record["id"]))
        status = str(result.get("status", "")) if result else ""
        if status not in {"succeeded", "failed"}:
            status = "failed"
            message = "CIW publication result is missing or invalid"
        else:
            message = str(result.get("message", ""))
        record["status"] = (
            "succeeded_with_warnings"
            if status == "succeeded" and record.get("warning_message")
            else status
        )
        record["publication_status"] = status
        record["publication_message"] = message
        record["finished_at"] = timestamp()
    payload["counts"] = counts(payload["tasks"])
    payload["status"] = overall_status(payload["tasks"])
    payload["finished_at"] = timestamp()
    write_status(manifest, payload)
    if payload["counts"]["canceled"]:
        return 130
    return 1 if payload["counts"]["failed"] else 0
