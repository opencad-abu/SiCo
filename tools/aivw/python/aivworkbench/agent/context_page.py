"""Immutable context pages with verified transport accounting."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping
from .protocol import ErrorCode, ProtocolError
from .redaction import redact as redact_secrets
from .context_values import canonical_json, freeze_json, thaw_json


@dataclass(frozen=True)
class ContextPage:
    index: int
    total: int
    items: tuple[Mapping[str, Any], ...]
    bytes_used: int
    truncated: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.index, int)
            or isinstance(self.index, bool)
            or self.index < 0
            or not isinstance(self.total, int)
            or isinstance(self.total, bool)
            or self.total <= 0
            or self.index >= self.total
        ):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context page index/total is invalid")
        if not isinstance(self.items, (tuple, list)):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context page items must be a sequence")
        if (
            not isinstance(self.bytes_used, int)
            or isinstance(self.bytes_used, bool)
            or self.bytes_used < 2
            or not isinstance(self.truncated, bool)
        ):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context page metadata is invalid")
        normalized: list[Mapping[str, Any]] = []
        for item in self.items:
            if not isinstance(item, Mapping):
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context page item must be an object")
            safe = redact_secrets(item)
            if not isinstance(safe, Mapping):
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context page item must be an object")
            frozen = freeze_json(safe)
            if not isinstance(frozen, Mapping):
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "context page item must be an object")
            normalized.append(frozen)
        actual = len(canonical_json(normalized).encode("utf-8"))
        if self.bytes_used != actual:
            raise ProtocolError(
                ErrorCode.CONTEXT_LIMIT_EXCEEDED,
                "context page bytes_used does not match canonical payload",
                {"declared": self.bytes_used, "actual": actual},
            )
        object.__setattr__(self, "items", tuple(normalized))

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "total": self.total,
            "items": [thaw_json(item) for item in self.items],
            "bytes_used": self.bytes_used,
            "truncated": self.truncated,
        }

    @property
    def transport_bytes(self) -> int:
        return len(canonical_json(self.to_dict()).encode("utf-8"))

