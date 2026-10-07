"""Provider boundary and action coercion helpers for AIVW runtime."""

from __future__ import annotations

import time

from .backend import ProviderRequest, ProviderResponse
from .protocol import Action, ProtocolError, ErrorCode
from .runtime_call import call_with_timeout, _BoundedCallTimeout

def _next_provider(runtime, request: ProviderRequest) -> ProviderResponse | Action:
    return call_with_timeout(
        lambda: runtime.provider.next_action(request),
        runtime._boundary_timeout(),
        name="provider-next-action",
    )


def _boundary_timeout(runtime) -> float:
    """Return the remaining deadline for one external boundary."""

    timeout = float(runtime.config.turn_timeout_seconds)
    if runtime._started_at is not None:
        remaining = float(runtime.config.total_timeout_seconds) - (
            time.monotonic() - runtime._started_at
        )
        if remaining <= 0:
            raise _BoundedCallTimeout("runtime total timeout exceeded")
        timeout = min(timeout, remaining)
    return timeout


def _coerce_action(runtime, response: ProviderResponse | Action) -> Action:
    if isinstance(response, Action):
        return response
    if not isinstance(response, ProviderResponse):
        raise ProtocolError(ErrorCode.INVALID_ACTION, "provider returned an unsupported response type")
    if response.error is not None:
        raise ProtocolError(response.error.code, response.error.message, response.error.details)
    if response.action is None:
        raise ProtocolError(ErrorCode.INVALID_ACTION, "provider response contains no action")
    return response.action
