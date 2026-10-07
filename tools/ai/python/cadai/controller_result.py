"""Read acknowledged results and project bounded replies across spool paths."""

from __future__ import annotations

import errno
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .runtime import read_spool
from .socket_server import RequestFailure


def result_payload(
    spool: Path, name: str, *, timeout: float, visibility_timeout: float,
    wait_for_visibility: bool,
) -> tuple[dict[str, Any], bytes]:
    try:
        deadline = time.monotonic() + (
            min(timeout, visibility_timeout) if wait_for_visibility else 0
        )
        while True:
            try:
                raw = read_spool(spool, name)
                break
            except OSError as exc:
                remaining = deadline - time.monotonic()
                if exc.errno not in {errno.ENOENT, errno.ESTALE} or remaining <= 0:
                    raise
                # Retry only reading the acknowledged result, never the
                # Virtuoso operation (which may already have mutated OA).
                time.sleep(min(0.05, remaining))
        value = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, RuntimeError) as exc:
        try:
            (spool / name).unlink(missing_ok=True)
        except OSError:
            pass
        raise RequestFailure("invalid_result", f"cannot read Virtuoso result: {exc}") from exc
    if not isinstance(value, dict):
        (spool / name).unlink(missing_ok=True)
        raise RequestFailure("invalid_result", "Virtuoso result must be a JSON object")
    return value, raw


def bounded_result(
    payload: dict[str, Any], name: str, raw: bytes, source_spool: Path,
    destination_spool: Path, *, inline_bytes: int, write_spool,
) -> dict[str, Any]:
    if len(raw) <= inline_bytes:
        (source_spool / name).unlink(missing_ok=True)
        return payload
    if source_spool != destination_spool:
        try:
            artifact = write_spool(destination_spool, "result", raw)
        finally:
            (source_spool / name).unlink(missing_ok=True)
    else:
        artifact = {
            "path": str(destination_spool / name),
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    return {
        "ok": True,
        "spooled": True,
        "artifact": {
            "path": artifact["path"],
            "size": artifact["size"],
            "sha256": artifact["sha256"],
        },
        "preview": str(payload.get("output") or payload.get("value") or "")[:8_192],
    }
