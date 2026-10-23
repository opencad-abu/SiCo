"""Journal record framing and validation, shared by index builders and readers."""

from __future__ import annotations

import json
import os

from ..core.contracts import TASK_CONTRACT, RunState
from cadai.json_values import strict_json

MAX_RECORD = 16 * 1024 * 1024


def fingerprint(fd):
    info = os.fstat(fd)
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def decode_record(raw, session_id, sequence, *, validate=True):
    row = strict_json(raw) if validate else json.loads(raw)
    if (not isinstance(row, dict) or row.get("contract") != TASK_CONTRACT
            or row.get("session_id") != session_id
            or type(row.get("sequence")) is not int or row["sequence"] != sequence
            or not isinstance(row.get("kind"), str)
            or not isinstance(row.get("payload"), dict)):
        raise ValueError("会话日志身份或顺序不匹配")
    return row


def validate_state(row):
    try:
        RunState.from_record(row["state"])
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid journal state") from exc


def iter_raw_records(stream, session_id, *, sequence=0, validate=True, checker=None,
                     end=None):
    """Yield complete records, leaving an unterminated final line unread.

    ``end`` fixes the snapshot size for readers racing a writer. A strict scan
    rejects malformed complete lines; only the final line lacking LF is skipped.
    """
    while end is None or stream.tell() < end:
        offset = stream.tell()
        limit = MAX_RECORD + 1 if end is None else min(MAX_RECORD + 1, end - offset)
        raw = stream.readline(limit)
        if len(raw) > MAX_RECORD:
            raise ValueError("会话记录超过单条上限")
        if not raw or not raw.endswith(b"\n"):
            stream.seek(offset)
            return
        sequence += 1
        row = decode_record(raw, session_id, sequence, validate=validate)
        if validate and checker is not None:
            checker(row)
        yield row, offset, len(raw)
