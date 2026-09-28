"""ASCII-only quick-input framing; user text is never evaluated by SKILL."""

from __future__ import annotations

import base64
import binascii

from .framing import strict_json
from .targets import submission


def encode_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 16000 or "\0" in text:
        raise ValueError("请输入需求，最多 16000 个字符。")
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def decode_submission(raw):
    message = strict_json(raw)
    if not isinstance(message, dict) or set(message) != {"kind", "id", "text_b64", "context"}:
        raise ValueError("Invalid encoded quick input")
    encoded = message.pop("text_b64")
    if not isinstance(encoded, str) or len(encoded) > 86000:
        raise ValueError("Encoded quick input exceeds limit")
    try:
        message["text"] = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise ValueError("Invalid UTF-8 quick input") from exc
    submission(message)
    return message
