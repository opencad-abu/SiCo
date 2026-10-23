"""Validate bounded host bridge payloads at the transport boundary."""

from __future__ import annotations

import json

from .framing import ProtocolError, strict_json
from .targets import submission

PAYLOAD_LIMIT = 512 * 1024


def capture_payload(value):
    """Return one validated, bounded payload without retaining a host handle."""
    try:
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False).encode("ascii")
        if len(raw) > PAYLOAD_LIMIT:
            raise ProtocolError("Host payload exceeds limit")
        row = strict_json(raw)
        if set(row) - {"attachment_text"} != {"message", "provider_config", "environment"}:
            # Keep the error text used by the service compatibility entry.
            raise ProtocolError("Invalid service message fields")
        submission(row["message"])
        if "attachment_text" in row and (not isinstance(row["attachment_text"], str)
                                         or "\0" in row["attachment_text"]):
            raise ProtocolError("Invalid host attachment")
        if row["provider_config"] is not None and not isinstance(row["provider_config"], dict):
            raise ProtocolError("Invalid host configuration")
        env = row["environment"]
        if not isinstance(env, dict) or len(env) > 2048 or any(
                not isinstance(v, str) or not k or "=" in k or "\0" in k or "\0" in v
                for k, v in env.items()):
            raise ProtocolError("Invalid host environment")
        return row
    except ProtocolError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        raise ProtocolError("Invalid host payload") from exc
