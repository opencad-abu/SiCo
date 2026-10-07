"""Immutable, redacted JSON values used at the AIVW protocol boundary.

This module owns value representation concerns shared by protocol objects and
their consumers.  It deliberately imports protocol errors lazily so the
protocol schema can use the codec without creating an import cycle.
"""


from __future__ import annotations


import math




from typing import Any, Mapping


from .redaction import redact as redact, redact_text as redact_text

class FrozenValueDict(dict):
    """A JSON-encoder-compatible mapping that rejects all mutations."""

    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("protocol mappings are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


def _error(code: str, message: str, details: Mapping[str, Any] | None = None) -> Exception:
    # Lazy import keeps protocol_errors -> value_codec one-way during module load.
    from .protocol_errors import ProtocolError

    return ProtocolError(code, message, details)


def _error_code(name: str) -> str:
    from .protocol_constants import ErrorCode

    return str(getattr(ErrorCode, name).value)


def freeze(value: Any, _seen: set[int] | None = None) -> Any:
    """Copy JSON-shaped data into immutable containers."""

    seen = set() if _seen is None else _seen
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            raise _error(_error_code("INVALID_ENVELOPE"), "protocol value contains a cyclic object")
        seen.add(identity)
        result = FrozenValueDict()
        try:
            for key, child in value.items():
                if not isinstance(key, str):
                    raise _error(_error_code("INVALID_ENVELOPE"), "protocol value contains a non-text key")
                dict.__setitem__(result, key, freeze(child, seen))
        finally:
            seen.remove(identity)
        return result
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in seen:
            raise _error(_error_code("INVALID_ENVELOPE"), "protocol value contains a cyclic object")
        seen.add(identity)
        try:
            return tuple(freeze(child, seen) for child in value)
        finally:
            seen.remove(identity)
    raise _error(_error_code("INVALID_ENVELOPE"), "protocol value contains a non-JSON value")


def thaw(value: Any) -> Any:
    """Return a wholly independent mutable copy for serialization callers."""

    if isinstance(value, Mapping):
        return {str(key): thaw(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(child) for child in value]
    return value


def ensure_json(value: object, path: str = "value", _seen: set[int] | None = None) -> None:
    """Validate JSON-compatible values, including finite numbers and cycles."""

    if _seen is None:
        _seen = set()
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _error(_error_code("INVALID_ENVELOPE"), "%s contains a non-finite number" % path)
        return
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in _seen:
            raise _error(_error_code("INVALID_ENVELOPE"), "%s contains a cyclic object" % path)
        _seen.add(identity)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise _error(_error_code("INVALID_ENVELOPE"), "%s has a non-text key" % path)
                ensure_json(item, "%s.%s" % (path, key), _seen)
        finally:
            _seen.remove(identity)
        return
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in _seen:
            raise _error(_error_code("INVALID_ENVELOPE"), "%s contains a cyclic object" % path)
        _seen.add(identity)
        try:
            for index, item in enumerate(value):
                ensure_json(item, "%s[%d]" % (path, index), _seen)
        finally:
            _seen.remove(identity)
        return
    raise _error(_error_code("INVALID_ENVELOPE"), "%s contains a non-JSON value" % path)


def reject_verdict_fields(
    value: object,
    path: str = "params",
    *,
    error_code: str = "INVALID_ACTION",
    message: str = "provider cannot return a verdict field",
    cycle_message: str | None = None,
    _seen: set[int] | None = None,
) -> None:
    """Reject model-owned verdict fields before they cross the protocol boundary."""

    if _seen is None:
        _seen = set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in _seen:
            raise _error(error_code, cycle_message or "%s contains a cyclic object" % path)
        _seen.add(identity)
        forbidden = {"verdict", "gate_verdict", "deterministic_verdict", "ai_verdict", "pass_fail"}
        try:
            for key, item in value.items():
                if str(key).lower() in forbidden:
                    raise _error(error_code, message, {"field": path + "." + str(key)})
                reject_verdict_fields(item, path + "." + str(key), error_code=error_code, message=message, cycle_message=cycle_message, _seen=_seen)
        finally:
            _seen.remove(identity)
    elif isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in _seen:
            raise _error(error_code, cycle_message or "%s contains a cyclic object" % path)
        _seen.add(identity)
        try:
            for index, item in enumerate(value):
                reject_verdict_fields(item, "%s[%d]" % (path, index), error_code=error_code, message=message, cycle_message=cycle_message, _seen=_seen)
        finally:
            _seen.remove(identity)


__all__ = [
    "FrozenValueDict",
    "ensure_json",
    "freeze",
    "redact",
    "redact_text",
    "reject_verdict_fields",
    "thaw",
]
