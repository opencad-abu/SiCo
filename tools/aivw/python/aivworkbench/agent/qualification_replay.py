"""Bind approved request/action vectors into replay records."""

from __future__ import annotations










from typing import Sequence

from .backend import (
    ProviderRequest,
)



from .protocol import (
    Action,
    ErrorCode,
    ProtocolError,
)



from .providers.replay import ReplayRecord, request_digest





def build_replay_records(requests: Sequence[ProviderRequest], actions: Sequence[Action], *, source_generation: str | None = None, template_lock: str | None = None) -> tuple[ReplayRecord, ...]:
    """Bind a deterministic action sequence to exact request digests.

    This helper is useful for recording an approved provider run into a
    Bundle/Replay fixture.  It rejects length, source-generation, template-lock
    and action-kind mismatches before producing records.
    """

    if len(requests) != len(actions) or not requests:
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "request/action vector lengths must match and be non-empty")
    records: list[ReplayRecord] = []
    for index, (request, action) in enumerate(zip(requests, actions)):
        if not isinstance(request, ProviderRequest) or not isinstance(action, Action):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "request/action vector contains an invalid item")
        expected_source = source_generation or request.source_generation
        if request.source_generation != expected_source or action.expected_source_generation != expected_source:
            raise ProtocolError(ErrorCode.STALE_SOURCE_GENERATION, "record source generation mismatch")
        expected_lock = template_lock if template_lock is not None else request.template_lock
        if request.template_lock != expected_lock or action.template_lock != expected_lock:
            raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "record template lock mismatch")
        records.append(
            ReplayRecord(
                request_sha256=request_digest(request),
                response={"action": action.to_dict()},
                source_generation=expected_source,
                template_lock=expected_lock,
                sequence=index,
            )
        )
    return tuple(records)

