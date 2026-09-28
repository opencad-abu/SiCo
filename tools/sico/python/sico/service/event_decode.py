"""Decode complete serialized values and verified offload envelopes for reading."""

import re

from ..transport.framing import strict_json
from .native_display import unpack

LITERAL_FIELDS = frozenset({
    "command", "cwd", "path", "diff", "script", "code", "expression", "regex", "pattern",
    "sha256", "url",
})
# Actual terminal control sequences only; literal backslashes in code stay intact.
TERMINAL_CONTROLS = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")


def decoded(value, key=""):
    """Only a complete JSON value establishes an escaping layer to remove."""
    if not isinstance(value, str) or key in LITERAL_FIELDS:
        return value
    for _ in range(4):
        if not isinstance(value, str):
            break
        candidate = value.strip()
        if candidate.startswith("```json\n") and candidate.endswith("\n```"):
            candidate = candidate[8:-4].strip()
        if not candidate or candidate[0] not in '{["':
            break
        try:
            wrapper = strict_json('{"value":' + candidate + '}')
        except ValueError:
            break
        if set(wrapper) != {"value"} or not isinstance(wrapper["value"], (dict, list, str)):
            break
        value = wrapper["value"]
    return TERMINAL_CONTROLS.sub("", value) if isinstance(value, str) else value


def normalized(value, key=""):
    value = decoded(value, key)
    if isinstance(value, dict):
        return {name: normalized(child, name) for name, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalized(child, key) for child in value]
    return value


def readable_payload(event, reader=None):
    """Resolve only protocol-owned offload fields, through the scoped artifact reader."""
    payload = dict(event["payload"])
    field = {"tool.started": "input", "tool.finished": "result",
             "codex.history.item": "value", "codex.native.updated": "value",
             "codex.plan.updated": "value", "codex.diff.updated": "value"}.get(event["kind"])
    envelope = payload.get(field)
    if isinstance(envelope, dict) and envelope.get("truncated") and envelope.get("artifact"):
        if reader is None:
            raise ValueError("完整消息需要读取归档，请重新打开记录")
        value = unpack(envelope, reader)
        payload[field] = {**value, **{key: child for key, child in envelope.items()
                                    if key not in {"truncated", "preview", "artifact"}}}
        payload["archive"] = envelope["artifact"]
    payload = normalized(payload)
    # Some adapters wrap an already structured result one extra time as
    # ``{"result": {"data": "{...}"}}``. Remove only that unambiguous
    # transport wrapper so the window does not show two nested “数据” headings.
    for name in ("input", "result", "value"):
        value = payload.get(name)
        if (isinstance(value, dict) and set(value) == {"data"}
                and isinstance(value["data"], dict)):
            payload[name] = value["data"]
    return payload
