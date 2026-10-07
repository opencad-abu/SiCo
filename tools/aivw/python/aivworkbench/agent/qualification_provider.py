"""Qualify provider request/response vectors and explicit fallbacks."""

from __future__ import annotations





import math


from pathlib import Path



from typing import Any, Iterable, Mapping

from .backend import (
    AgentProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderSession,
    ProviderUnavailable,
)



from .protocol import (
    Action,
    ErrorCode,
    ProtocolError,
)


from .providers.bundle import BundleProvider

from .providers.replay import ReplayProvider, ReplayRecord, request_digest



from .qualification_contract import QUALIFICATION_BLOCKED_INPUT, QUALIFICATION_BLOCKED_PROVIDER, QUALIFICATION_PASS, _PROVIDER_FAILURE_CODES, _now, _provider_identity, _safe_generation, _safe_template_lock, _validate_generation, _validate_template_lock
from .qualification_models import ProviderExchange, QualificationReport
from .qualification_exchange import _bounded_provider_call, _exchange_error, _failure_status, _response_payload
from .qualification_acceptance import _check_action_binding
from .qualification_candidate import _merge_workflow_fallback

def qualify_provider(
    provider: AgentProvider,
    requests: ProviderRequest | Iterable[ProviderRequest],
    *,
    expected_action_kinds: Iterable[str] | None = None,
    source_generation: str | None = None,
    template_lock: str | None = None,
    runtime_identity: Mapping[str, Any] | None = None,
    start_session: bool = True,
    metadata: Mapping[str, Any] | None = None,
    call_timeout_seconds: float = 20.0,
    expected_model_id: str | None = None,
) -> QualificationReport:
    """Qualify provider request/response vectors without implicit retries.

    The function performs at most one ``next_action`` call per supplied
    request.  A provider timeout, disconnect, malformed response, or stale
    binding becomes an explicit non-PASS report; callers may then invoke an
    explicitly selected fallback provider and retain both reports.
    """

    if (
        not isinstance(call_timeout_seconds, (int, float))
        or isinstance(call_timeout_seconds, bool)
        or not math.isfinite(float(call_timeout_seconds))
        or float(call_timeout_seconds) <= 0
    ):
        raise ProtocolError(
            ErrorCode.INVALID_ARGUMENTS,
            "qualification call timeout must be positive",
        )
    try:
        vector = [requests] if isinstance(requests, ProviderRequest) else list(requests)
    except (TypeError, ValueError):
        vector = []
    started = _now()
    errors: list[Mapping[str, Any]] = []
    exchanges: list[ProviderExchange] = []
    fallback_source = _safe_generation(source_generation)
    fallback_lock = _safe_template_lock(template_lock)
    if not vector or any(not isinstance(item, ProviderRequest) for item in vector):
        return QualificationReport(
            provider=_provider_identity(provider),
            protocol_version="aivw-agent-v1",
            runtime_identity=dict(runtime_identity or {}),
            source_generation=fallback_source,
            template_lock=fallback_lock,
            status=QUALIFICATION_BLOCKED_INPUT,
            errors=({"code": ErrorCode.INVALID_ARGUMENTS.value, "message": "qualification requests are invalid"},),
            metadata=dict(metadata or {}),
            started_at=started,
            finished_at=_now(),
        )
    active_source = source_generation or vector[0].source_generation
    active_lock = template_lock if template_lock is not None else vector[0].template_lock
    try:
        _validate_generation(active_source, "qualification source_generation")
        _validate_template_lock(active_lock, "qualification template_lock")
    except ProtocolError as exc:
        return QualificationReport(
            provider=_provider_identity(provider),
            protocol_version=vector[0].protocol_version,
            runtime_identity=dict(runtime_identity or {}),
            source_generation=fallback_source,
            template_lock=_safe_template_lock(active_lock),
            status=QUALIFICATION_BLOCKED_INPUT,
            errors=({"code": exc.code, "message": exc.message, "details": exc.details},),
            metadata=dict(metadata or {}),
            started_at=started,
            finished_at=_now(),
        )
    allowed_kinds = None if expected_action_kinds is None else frozenset(str(item) for item in expected_action_kinds)
    if expected_model_id is not None and (
        not isinstance(expected_model_id, str)
        or not expected_model_id
        or len(expected_model_id) > 256
        or any(char.isspace() or ord(char) < 0x20 for char in expected_model_id)
    ):
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification expected_model_id is invalid")
    session: ProviderSession | None = None
    if start_session:
        try:
            session = _bounded_provider_call(
                lambda: provider.start(vector[0]),
                float(call_timeout_seconds),
                "provider.start",
            )
        except BaseException as exc:
            error = _exchange_error(exc)
            errors.append(error)
            return QualificationReport(
                provider=_provider_identity(provider),
                protocol_version=vector[0].protocol_version,
                runtime_identity=dict(runtime_identity or {}),
                source_generation=active_source,
                template_lock=active_lock,
                status=QUALIFICATION_BLOCKED_PROVIDER,
                errors=tuple(errors),
                metadata=dict(metadata or {}),
                started_at=started,
                finished_at=_now(),
            )
    for index, request in enumerate(vector):
        request_payload = request.to_dict()
        request_hash = request_digest(request)
        if request.source_generation != active_source or request.template_lock != active_lock:
            errors.append({"code": ErrorCode.INVALID_ARGUMENTS.value, "message": "qualification vector binding mismatch", "index": index})
            exchanges.append(
                ProviderExchange(
                    index,
                    request_hash,
                    None,
                    getattr(provider, "name", type(provider).__name__),
                    None,
                    QUALIFICATION_BLOCKED_INPUT,
                    request_payload,
                    error=errors[-1],
                    session_id=None if session is None else session.session_id,
                    provider_response_valid=False,
                    runtime_action_accepted=False,
                )
            )
            continue
        try:
            response = _bounded_provider_call(
                lambda request=request: provider.next_action(request),
                float(call_timeout_seconds),
                "provider.next_action",
            )
            payload, response_hash, response_provider, model_id = _response_payload(response)
            action: Action | None = response.action if isinstance(response, ProviderResponse) else response if isinstance(response, Action) else None
            if action is None:
                response_error = (
                    response.error
                    if isinstance(response, ProviderResponse)
                    else None
                )
                error = (
                    response_error.to_dict()
                    if response_error is not None
                    else {
                        "code": ErrorCode.INVALID_ACTION.value,
                        "message": "provider response contains no action",
                    }
                )
                errors.append(error)
                response_status = (
                    QUALIFICATION_BLOCKED_PROVIDER
                    if response_error is not None
                    and response_error.code in _PROVIDER_FAILURE_CODES
                    else QUALIFICATION_BLOCKED_INPUT
                )
                exchanges.append(
                    ProviderExchange(
                        index,
                        request_hash,
                        response_hash,
                        response_provider or getattr(provider, "name", type(provider).__name__),
                        model_id,
                        response_status,
                        request_payload,
                        response=payload,
                        error=error,
                        session_id=None if session is None else session.session_id,
                        provider_response_valid=False,
                        runtime_action_accepted=False,
                    )
                )
                continue
            if action is not None:
                try:
                    _check_action_binding(action, request)
                    if allowed_kinds is not None and action.kind not in allowed_kinds:
                        raise ProtocolError(ErrorCode.INVALID_ACTION, "provider action kind is outside qualification vector", {"kind": action.kind})
                    if expected_model_id is not None and model_id != expected_model_id:
                        raise ProtocolError(
                            ErrorCode.INVALID_ACTION,
                            "provider model identity does not match qualification expectation",
                            {"expected_model_id": expected_model_id},
                        )
                except ProtocolError as exc:
                    error = _exchange_error(exc)
                    errors.append(error)
                    exchanges.append(
                        ProviderExchange(
                            index,
                            request_hash,
                            response_hash,
                            response_provider or getattr(provider, "name", type(provider).__name__),
                            model_id,
                            QUALIFICATION_BLOCKED_INPUT,
                            request_payload,
                            response=payload,
                            error=error,
                            session_id=None if session is None else session.session_id,
                            provider_response_valid=True,
                            runtime_action_accepted=False,
                        )
                    )
                    continue
            exchanges.append(
                ProviderExchange(index, request_hash, response_hash, response_provider or getattr(provider, "name", type(provider).__name__), model_id, QUALIFICATION_PASS, request_payload, response=payload, session_id=None if session is None else session.session_id)
            )
        except (ProviderUnavailable, ProtocolError, TimeoutError) as exc:
            error = _exchange_error(exc)
            errors.append(error)
            status = _failure_status(exc)
            exchanges.append(
                ProviderExchange(index, request_hash, None, getattr(provider, "name", type(provider).__name__), None, status, request_payload, error=error, session_id=None if session is None else session.session_id, provider_response_valid=False, runtime_action_accepted=False)
            )
        except BaseException as exc:
            error = _exchange_error(exc)
            errors.append(error)
            exchanges.append(
                ProviderExchange(index, request_hash, None, getattr(provider, "name", type(provider).__name__), None, QUALIFICATION_BLOCKED_PROVIDER, request_payload, error=error, session_id=None if session is None else session.session_id, provider_response_valid=False, runtime_action_accepted=False)
            )
    if errors:
        status = QUALIFICATION_BLOCKED_PROVIDER if any(item.status == QUALIFICATION_BLOCKED_PROVIDER for item in exchanges) else QUALIFICATION_BLOCKED_INPUT
    else:
        status = QUALIFICATION_PASS
    return QualificationReport(
        provider=_provider_identity(provider),
        protocol_version=vector[0].protocol_version,
        runtime_identity=dict(runtime_identity or {}),
        source_generation=active_source,
        template_lock=active_lock,
        status=status,
        exchanges=tuple(exchanges),
        errors=tuple(errors),
        metadata=dict(metadata or {}),
        started_at=started,
        finished_at=_now(),
    )


def qualify_bundle_provider(
    root: str | Path,
    requests: ProviderRequest | Iterable[ProviderRequest],
    *,
    expected_action_kinds: Iterable[str] | None = None,
    source_generation: str | None = None,
    template_lock: str | None = None,
    runtime_identity: Mapping[str, Any] | None = None,
    call_timeout_seconds: float = 20.0,
) -> QualificationReport:
    """Import and qualify an offline response bundle first (the M2 order)."""

    try:
        provider = BundleProvider(root, source_generation=source_generation, template_lock=template_lock)
    except (ProtocolError, OSError, ValueError) as exc:
        return QualificationReport(
            provider={"name": "bundle", "class": "aivworkbench.agent.providers.bundle.BundleProvider"},
            protocol_version="aivw-agent-v1",
            runtime_identity=dict(runtime_identity or {}),
            source_generation=_safe_generation(source_generation),
            template_lock=_safe_template_lock(template_lock),
            status=QUALIFICATION_BLOCKED_INPUT,
            errors=(_exchange_error(exc),),
            metadata={"bundle_root": str(root)},
            started_at=_now(),
            finished_at=_now(),
        )
    return qualify_provider(
        provider,
        requests,
        expected_action_kinds=expected_action_kinds,
        source_generation=source_generation,
        template_lock=template_lock,
        runtime_identity=runtime_identity,
        metadata={"bundle_root": str(provider.root)},
        call_timeout_seconds=call_timeout_seconds,
    )


def qualify_replay_provider(
    records: Iterable[ReplayRecord | Mapping[str, Any]],
    requests: ProviderRequest | Iterable[ProviderRequest],
    *,
    expected_action_kinds: Iterable[str] | None = None,
    source_generation: str | None = None,
    template_lock: str | None = None,
    runtime_identity: Mapping[str, Any] | None = None,
    call_timeout_seconds: float = 20.0,
) -> QualificationReport:
    provider = ReplayProvider(records)
    return qualify_provider(
        provider,
        requests,
        expected_action_kinds=expected_action_kinds,
        source_generation=source_generation,
        template_lock=template_lock,
        runtime_identity=runtime_identity,
        metadata={"offline_fallback": True},
        call_timeout_seconds=call_timeout_seconds,
    )


def qualify_with_fallback(
    provider: AgentProvider,
    fallback: AgentProvider,
    request: ProviderRequest,
    *,
    expected_action_kinds: Iterable[str] | None = None,
    runtime_identity: Mapping[str, Any] | None = None,
    call_timeout_seconds: float = 20.0,
) -> QualificationReport:
    """Qualify a primary provider and explicitly-selected fallback.

    The fallback is invoked only after a completed primary qualification
    report is non-PASS.  The resulting report retains the primary exchange
    and marks the fallback identity; there is no hidden retry loop.
    """

    primary = qualify_provider(
        provider,
        request,
        expected_action_kinds=expected_action_kinds,
        runtime_identity=runtime_identity,
        call_timeout_seconds=call_timeout_seconds,
    )
    if primary.passed:
        return primary
    secondary = qualify_provider(
        fallback,
        request,
        expected_action_kinds=expected_action_kinds,
        runtime_identity=runtime_identity,
        metadata={"explicit_fallback": True, "primary_status": primary.status},
        call_timeout_seconds=call_timeout_seconds,
    )
    return _merge_workflow_fallback(primary, secondary)

