"""Immutable DataHub envelope DTOs with one strict wire representation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from sico_datahub_wire import DEFAULT_DEADLINE_MS, HUB, validate_envelope

from .errors import HubError, may_retry


@dataclass(frozen=True)
class Page:
    offset: int
    limit: int
    total: int
    next: Optional[int]
    truncated: bool

    def to_dict(self) -> Dict[str, object]:
        return {
            "offset": self.offset,
            "limit": self.limit,
            "total": self.total,
            "next": self.next,
            "truncated": self.truncated,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "Page":
        if not isinstance(value, dict):
            raise HubError("invalid_arguments", "Page must be an object")
        return cls(
            offset=value.get("offset"),
            limit=value.get("limit"),
            total=value.get("total"),
            next=value.get("next"),
            truncated=value.get("truncated"),
        )


@dataclass(frozen=True)
class Meta:
    trace_id: str = ""
    source: str = "python"
    target: str = "python"
    deadline_ms: int = DEFAULT_DEADLINE_MS

    def to_dict(self) -> Dict[str, object]:
        return {
            "trace_id": self.trace_id,
            "source": self.source,
            "target": self.target,
            "deadline_ms": self.deadline_ms,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "Meta":
        if not isinstance(value, dict):
            raise HubError("invalid_arguments", "Meta must be an object")
        return cls(
            trace_id=value.get("trace_id"),
            source=value.get("source"),
            target=value.get("target"),
            deadline_ms=value.get("deadline_ms"),
        )


@dataclass(frozen=True)
class Error:
    code: str
    message: str
    details: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, value: dict) -> "Error":
        if not isinstance(value, dict):
            raise HubError("invalid_arguments", "Error must be an object")
        return cls(
            code=value.get("code"),
            message=value.get("message"),
            details=dict(value.get("details") or {}),
        )

    @classmethod
    def from_exception(cls, exc: HubError) -> "Error":
        return cls(code=exc.code, message=exc.message, details=dict(exc.details))


@dataclass(frozen=True)
class Envelope:
    contract: str
    action: str
    id: str
    payload: Dict[str, object] = field(default_factory=dict)
    status: str = "ok"
    page: Optional[Page] = None
    meta: Meta = field(default_factory=Meta)
    error: Optional[Error] = None
    version: int = 1
    hub: str = HUB

    def __post_init__(self):
        if not isinstance(self.payload, dict):
            raise HubError("invalid_arguments", "Envelope payload must be an object")
        if not isinstance(self.meta, Meta):
            raise HubError("invalid_arguments", "Envelope meta must be Meta")
        if self.page is not None and not isinstance(self.page, Page):
            raise HubError("invalid_arguments", "Envelope page must be Page")
        if self.error is not None and not isinstance(self.error, Error):
            raise HubError("invalid_arguments", "Envelope error must be Error")
        validate_envelope(self.to_dict())

    def to_dict(self) -> Dict[str, object]:
        row = {
            "hub": self.hub,
            "contract": self.contract,
            "version": self.version,
            "id": self.id,
            "action": self.action,
            "status": self.status,
            "payload": dict(self.payload),
            "meta": self.meta.to_dict(),
            "error": self.error.to_dict() if self.error is not None else None,
        }
        if self.page is not None:
            row["page"] = self.page.to_dict()
        return row

    @classmethod
    def from_dict(cls, value: dict) -> "Envelope":
        normalized = validate_envelope(value)
        page = normalized.get("page")
        error = normalized["error"]
        return cls(
            contract=normalized["contract"],
            action=normalized["action"],
            id=normalized["id"],
            payload=dict(normalized["payload"]),
            status=normalized["status"],
            page=Page.from_dict(page) if page is not None else None,
            meta=Meta.from_dict(normalized["meta"]),
            error=Error.from_dict(error) if error is not None else None,
            version=normalized["version"],
            hub=normalized["hub"],
        )

    @classmethod
    def failure(
        cls,
        contract: str,
        action: str,
        request_id: str,
        code: str,
        message: str,
        *,
        details: Optional[dict] = None,
        meta: Optional[Meta] = None,
    ) -> "Envelope":
        return cls(
            contract=contract,
            action=action,
            id=request_id,
            status="error",
            error=Error(code, message, dict(details or {})),
            meta=meta or Meta(),
        )

    @classmethod
    def from_exception(
        cls,
        contract: str,
        action: str,
        request_id: str,
        exc: HubError,
        *,
        meta: Optional[Meta] = None,
    ) -> "Envelope":
        return cls.failure(
            contract,
            action,
            request_id,
            exc.code,
            exc.message,
            details=exc.details,
            meta=meta,
        )

    def may_retry(self, *, write: bool = False) -> bool:
        code = self.error.code if self.error is not None else ""
        return may_retry(self.status, code, write=write)


__all__ = ["Envelope", "Error", "Meta", "Page"]
