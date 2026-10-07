"""Convert provider calls into bounded auditable exchanges."""

from __future__ import annotations









import threading

from typing import Any, Callable

from .backend import (
    AgentProvider,
    ProviderResponse,
    ProviderUnavailable,
)

from .context import redact_secrets


from .protocol import (
    Action,
    ErrorCode,
    ProtocolError,
)

from .value_codec import thaw


from .providers.replay import request_digest



from .qualification_contract import QUALIFICATION_BLOCKED_INPUT, QUALIFICATION_BLOCKED_PROVIDER, QUALIFICATION_PASS, _PROVIDER_FAILURE_CODES, _assert_no_forbidden_verdict, _raw_digest
from .qualification_models import ProviderExchange, _ProviderCall

def _bounded_provider_call(
    function: Callable[[], Any],
    timeout_seconds: float,
    operation: str,
) -> Any:
    """Apply a wall-clock bound without assuming provider transport support."""

    completed = threading.Event()
    outcome: dict[str, Any] = {}

    def invoke() -> None:
        try:
            outcome["value"] = function()
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            completed.set()

    worker = threading.Thread(
        target=invoke,
        name="aivw-qualification-%s" % operation.replace(".", "-"),
        daemon=True,
    )
    worker.start()
    if not completed.wait(timeout_seconds):
        raise ProviderUnavailable(
            ErrorCode.PROVIDER_TIMEOUT.value,
            "%s exceeded %.6g seconds" % (operation, timeout_seconds),
        )
    error = outcome.get("error")
    if isinstance(error, BaseException):
        raise error
    return outcome.get("value")


def _response_payload(response: ProviderResponse | Action) -> tuple[dict[str, Any], str, str | None, str | None]:
    if isinstance(response, Action):
        payload = {"action": response.to_dict()}
        return payload, _raw_digest(payload), None, None
    if not isinstance(response, ProviderResponse):
        raise ProtocolError(ErrorCode.INVALID_ACTION, "provider returned an unsupported response type")
    if response.error is not None:
        payload = {"error": response.error.to_dict()}
        return payload, _raw_digest(payload), response.provider, response.model_id
    if response.action is None:
        raise ProtocolError(ErrorCode.INVALID_ACTION, "provider response contains no action")
    payload = {
        "action": response.action.to_dict(),
        "provider": response.provider,
        "model_id": response.model_id,
        "usage": thaw(response.usage),
        "metadata": thaw(response.raw_metadata),
    }
    _assert_no_forbidden_verdict(payload, "provider_response")
    return payload, _raw_digest(payload), response.provider, response.model_id


def _exchange_error(exc: BaseException) -> dict[str, Any]:
    code = getattr(exc, "code", ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value)
    if isinstance(code, ErrorCode):
        code = code.value
    message = str(exc)
    # ProviderUnavailable and ProtocolError already redact, but run through the
    # common path for arbitrary injected transport exceptions as well.
    return {
        "code": str(code),
        "type": type(exc).__name__,
        "message": redact_secrets(message),
    }


def _failure_status(exc: BaseException) -> str:
    code = getattr(exc, "code", "")
    if isinstance(code, ErrorCode):
        code = code.value
    if isinstance(exc, (ProviderUnavailable, TimeoutError)) or str(code) in _PROVIDER_FAILURE_CODES:
        return QUALIFICATION_BLOCKED_PROVIDER
    return QUALIFICATION_BLOCKED_INPUT


def _action_from_response(response: object) -> Action | None:
    if isinstance(response, Action):
        return response
    if isinstance(response, ProviderResponse):
        return response.action
    return None


def _exchange_from_call(
    call: _ProviderCall,
    index: int,
    provider: AgentProvider,
    *,
    runtime_action_accepted: bool | None = None,
) -> ProviderExchange:
    request_payload = call.request.to_dict()
    request_hash = request_digest(call.request)
    if call.error is not None:
        # A qualification-layer rejection can happen after the underlying
        # provider returned a typed response (for example, an exact model
        # identity mismatch).  Preserve that response evidence while marking
        # the runtime boundary as rejected.  Transport/provider exceptions
        # still take the compact error-only path below.
        if call.response is not None:
            try:
                payload, response_hash, response_provider, model_id = _response_payload(call.response)
            except (ProtocolError, ValueError, TypeError):
                payload = None
                response_hash = None
                response_provider = None
                model_id = None
            if payload is not None:
                return ProviderExchange(
                    index=index,
                    request_sha256=request_hash,
                    response_sha256=response_hash,
                    provider=response_provider or getattr(provider, "name", type(provider).__name__),
                    model_id=model_id,
                    status=QUALIFICATION_BLOCKED_INPUT,
                    request=request_payload,
                    response=payload,
                    error=_exchange_error(call.error),
                    session_id=call.session_id,
                    provider_response_valid=True,
                    runtime_action_accepted=False
                    if runtime_action_accepted is None
                    else runtime_action_accepted,
                )
        error = _exchange_error(call.error)
        return ProviderExchange(
            index=index,
            request_sha256=request_hash,
            response_sha256=None,
            provider=getattr(provider, "name", type(provider).__name__),
            model_id=None,
            status=_failure_status(call.error),
            request=request_payload,
            error=error,
            session_id=call.session_id,
            provider_response_valid=False,
            runtime_action_accepted=False
            if runtime_action_accepted is None
            else runtime_action_accepted,
        )
    try:
        payload, response_hash, response_provider, model_id = _response_payload(call.response)  # type: ignore[arg-type]
    except (ProtocolError, ValueError, TypeError) as exc:
        return ProviderExchange(
            index=index,
            request_sha256=request_hash,
            response_sha256=None,
            provider=getattr(provider, "name", type(provider).__name__),
            model_id=None,
            status=QUALIFICATION_BLOCKED_INPUT,
            request=request_payload,
            error=_exchange_error(exc),
            session_id=call.session_id,
            provider_response_valid=False,
            runtime_action_accepted=False
            if runtime_action_accepted is None
            else runtime_action_accepted,
        )
    # A typed ProviderResponse carrying ``error`` is still a provider-boundary
    # message, but it is not a usable action response.  Keep its digest and
    # safe error payload for audit purposes while distinguishing it from a
    # syntactically valid action that the runtime later rejects.
    if _action_from_response(call.response) is None:
        response_error = None
        response_status = QUALIFICATION_BLOCKED_INPUT
        if isinstance(call.response, ProviderResponse) and call.response.error is not None:
            response_error = call.response.error.to_dict()
            response_status = (
                QUALIFICATION_BLOCKED_PROVIDER
                if call.response.error.code in _PROVIDER_FAILURE_CODES
                else QUALIFICATION_BLOCKED_INPUT
            )
        return ProviderExchange(
            index=index,
            request_sha256=request_hash,
            response_sha256=response_hash,
            provider=response_provider or getattr(provider, "name", type(provider).__name__),
            model_id=model_id,
            status=response_status,
            request=request_payload,
            response=payload,
            error=response_error,
            session_id=call.session_id,
            provider_response_valid=False,
            runtime_action_accepted=False
            if runtime_action_accepted is None
            else runtime_action_accepted,
        )
    return ProviderExchange(
        index=index,
        request_sha256=request_hash,
        response_sha256=response_hash,
        provider=response_provider or getattr(provider, "name", type(provider).__name__),
        model_id=model_id,
        status=QUALIFICATION_PASS,
        request=request_payload,
        response=payload,
        session_id=call.session_id,
        provider_response_valid=True,
        runtime_action_accepted=runtime_action_accepted,
    )

