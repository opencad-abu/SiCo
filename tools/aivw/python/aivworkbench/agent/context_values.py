"""Immutable canonical JSON values for bounded context snapshots."""

from __future__ import annotations
import json
from typing import Any, Mapping
from .protocol import ErrorCode, ProtocolError


class _FrozenDict(dict):
    """A JSON-compatible dict that cannot be mutated after construction."""

    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("context mappings are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    # Keep Python 3.9's in-place dict union from bypassing the frozen
    # mapping methods (``mapping |= other`` otherwise calls ``dict.__ior__``).
    __ior__ = _immutable


def freeze_json(value: Any, seen: set[int] | None = None) -> Any:
    """Copy JSON-shaped data into immutable containers.

    ``dataclass(frozen=True)`` protects only the outer object.  Context and
    page payloads cross persistence/provider boundaries, so nested mappings
    and sequences must be immutable as well.  A dict subclass is used instead
    of ``MappingProxyType`` because the standard JSON encoder understands it.
    """
    if seen is None:
        seen = set()
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context contains a cyclic object")
        seen.add(identity)
        result = _FrozenDict()
        try:
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context contains a non-text key")
                # Bypass the mutating override while constructing the copy.
                dict.__setitem__(result, key, freeze_json(child, seen))
        finally:
            seen.remove(identity)
        return result
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in seen:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context contains a cyclic object")
        seen.add(identity)
        try:
            return tuple(freeze_json(child, seen) for child in value)
        finally:
            seen.remove(identity)
    raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context contains a non-JSON value")


def thaw_json(value: Any) -> Any:
    """Return a defensive mutable copy for callers that need a plain dict."""
    if isinstance(value, Mapping):
        return {str(key): thaw_json(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw_json(child) for child in value]
    return value


def canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context contains a non-JSON value", {"detail": str(exc)}) from exc

