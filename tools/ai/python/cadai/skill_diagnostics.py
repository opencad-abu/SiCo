"""Bounded per-tool SKILL diagnostics, kept outside business data and identities."""

import json
from contextvars import ContextVar


_ACTIVE = ContextVar("cadai_skill_diagnostics", default=None)
FIELDS = ("output", "output_truncated", "output_capture")
_MAX_BYTES = 65536
_MAX_JSON_BYTES = 80000


def output_fields(detail):
    """Accept only the explicit capture contract, including legacy error envelopes."""
    seen = set()
    while isinstance(detail, dict) and id(detail) not in seen:
        seen.add(id(detail))
        if detail.get("output_capture") == "skill_ports":
            return {key: detail[key] for key in FIELDS if key in detail}
        detail = detail.get("data")
    return {}


def carry_output(result, source):
    """Carry diagnostics across a result conversion, without recording them again.

    A quiet transport envelope must not erase retained producer output. This is
    forwarding, not aggregation: an outer envelope owns its nonempty capture.
    """
    fields = output_fields(source)
    if not fields or (not fields.get("output") and not fields.get("output_truncated")
                      and output_fields(result)):
        return result
    return {**result, **fields}


def record_output(detail):
    """Record once at the decoding boundary; transformations only carry output."""
    scope = _ACTIVE.get()
    if scope is not None:
        scope.add(detail)


class SkillDiagnostics:
    """One request owns its output; nested requests and concurrent tasks are isolated."""

    def __init__(self):
        self._text = ""
        self._truncated = False
        self._captured = False
        self._full = False

    def __enter__(self):
        self._token = _ACTIVE.set(self)
        return self

    def __exit__(self, *_):
        _ACTIVE.reset(self._token)

    def add(self, detail):
        fields = output_fields(detail)
        if not fields:
            return
        self._captured = True
        truncated = fields.get("output_truncated") is True
        self._truncated |= truncated
        text = fields.get("output", "")
        if not isinstance(text, str):
            return
        if self._full:
            self._truncated |= bool(text)
            return
        available = max(0, _MAX_BYTES - len(self._text.encode("utf-8")))
        raw = text.encode("utf-8")
        prefix = raw[:available].decode("utf-8", errors="ignore")
        self._text += prefix
        self._truncated |= len(prefix.encode("utf-8")) < len(raw)
        self._full = truncated or len(prefix.encode("utf-8")) < len(raw)
        while len(json.dumps(self._text, ensure_ascii=False).encode("utf-8")) > _MAX_JSON_BYTES:
            encoded = self._text.encode("utf-8")
            self._text = encoded[:int(len(encoded) * .75)].decode("utf-8", errors="ignore")
            self._truncated = True
            self._full = True

    def attach(self, result):
        if not self._captured:
            return result
        # Retained outcomes already carry their original capture, possibly from a
        # different request. Do not replace those with this request's quiet reads.
        return carry_output(result, {"output": self._text, "output_truncated": self._truncated,
                                     "output_capture": "skill_ports"})
