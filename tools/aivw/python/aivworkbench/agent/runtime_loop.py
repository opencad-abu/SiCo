"""Bounded provider/broker turn loop for :mod:`agent.runtime`."""

from __future__ import annotations

from typing import Any, Mapping
import time

from .backend import ProviderUnavailable
from .protocol import AgentError, ErrorCode, EventType, ProtocolError, ActionKind
from .runtime_contract import AgentState
from .runtime_call import call_with_timeout


def run(runtime: Any, *, initial_context: Mapping[str, Any] | None = None):
    with runtime._lock:
        if runtime.state in AgentState.TERMINAL:
            return runtime.result()
        if initial_context is not None:
            if not isinstance(initial_context, Mapping):
                runtime._finish_error(
                    AgentState.BLOCKED,
                    AgentError(ErrorCode.INVALID_ARGUMENTS.value, "initial context must be an object"),
                )
                return runtime.result()
            if runtime._initial_context is not None and dict(runtime._initial_context) != dict(initial_context):
                runtime._finish_error(
                    AgentState.BLOCKED,
                    AgentError(
                        ErrorCode.INVALID_ARGUMENTS.value,
                        "initial context cannot change after a run has started",
                    ),
                )
                return runtime.result()
            runtime._initial_context = dict(initial_context)
        if runtime._started_at is None:
            runtime._started_at = time.monotonic()
            runtime._emit(EventType.SESSION_RESUMED if runtime.sequence else EventType.SESSION_STARTED, {"provider": getattr(runtime.provider, "name", type(runtime.provider).__name__)})
        starting = runtime.state == AgentState.INIT
        if runtime.session is None:
            try:
                context = runtime._build_context()
                request = runtime._provider_request(context)
                runtime.session = call_with_timeout(
                    lambda: runtime.provider.start(request),
                    runtime._boundary_timeout(),
                    name="provider-start",
                )
                runtime._provider_session_ready = True
            except ProviderUnavailable as exc:
                runtime._provider_session_ready = False
                code = getattr(exc, "code", ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value)
                runtime._emit(EventType.PROVIDER_UNAVAILABLE, {"code": code, "detail": str(exc)})
                runtime._finish_error(AgentState.BLOCKED, AgentError(code, str(exc)))
                return runtime.result()
            except TimeoutError as exc:
                runtime._provider_session_ready = False
                decision = runtime.recovery.classify(exc, operation="provider.start", provider=True)
                runtime._finish_error(AgentState.TIMEOUT, decision.to_error())
                return runtime.result()
            except ProtocolError as exc:
                runtime._provider_session_ready = False
                runtime._finish_error(AgentState.BLOCKED, exc.to_error())
                return runtime.result()
            except BaseException as exc:
                runtime._provider_session_ready = False
                decision = runtime.recovery.classify(exc, operation="provider.start", provider=True)
                runtime._finish_error(AgentState.BLOCKED, decision.to_error())
                return runtime.result()
        elif not runtime._provider_session_ready:
            # A non-terminal checkpoint may be resumed directly after a
            # process crash.  Restore the provider session without
            # rewinding the AIVW business state to input qualification.
            try:
                context = runtime._build_context()
                request = runtime._provider_request(context)
                runtime.session = call_with_timeout(
                    lambda: runtime.provider.resume(runtime.session, request),
                    runtime._boundary_timeout(),
                    name="provider-resume",
                )
                runtime._provider_session_ready = True
            except ProviderUnavailable as exc:
                runtime._provider_session_ready = False
                code = getattr(exc, "code", ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value)
                runtime._emit(EventType.PROVIDER_UNAVAILABLE, {"code": code, "detail": str(exc)})
                runtime._finish_error(AgentState.BLOCKED, AgentError(code, str(exc)))
                return runtime.result()
            except TimeoutError as exc:
                runtime._provider_session_ready = False
                decision = runtime.recovery.classify(exc, operation="provider.resume", provider=True)
                runtime._finish_error(AgentState.TIMEOUT, decision.to_error())
                return runtime.result()
            except ProtocolError as exc:
                runtime._provider_session_ready = False
                runtime._finish_error(AgentState.BLOCKED, exc.to_error())
                return runtime.result()
            except BaseException as exc:
                runtime._provider_session_ready = False
                decision = runtime.recovery.classify(exc, operation="provider.resume", provider=True)
                runtime._finish_error(AgentState.BLOCKED, decision.to_error())
                return runtime.result()
        if starting:
            runtime._transition(AgentState.INPUT_QUALIFICATION)
        runtime._checkpoint("session-start")
    while True:
        with runtime._lock:
            if runtime.state in AgentState.TERMINAL:
                return runtime.result()
            if runtime._interrupt_requested.is_set():
                runtime._interrupt()
                return runtime.result()
            if runtime.turns >= runtime.config.max_turns:
                runtime._finish_error(AgentState.BLOCKED, AgentError(ErrorCode.BUDGET_EXCEEDED.value, "turn budget exhausted"))
                return runtime.result()
            if runtime._started_at is not None and time.monotonic() - runtime._started_at > runtime.config.total_timeout_seconds:
                runtime._finish_error(AgentState.TIMEOUT, AgentError(ErrorCode.PROVIDER_TIMEOUT.value, "runtime total timeout exceeded"))
                return runtime.result()
            runtime.turns += 1
            runtime.turn_id = "%s-turn-%04d" % (runtime.run_id, runtime.turns)
            runtime._emit(EventType.TURN_STARTED, {"turn": runtime.turns}, turn_id=runtime.turn_id)
            try:
                context = runtime._build_context()
            except ProtocolError as exc:
                runtime._finish_error(AgentState.BLOCKED, exc.to_error())
                return runtime.result()
            runtime._emit(EventType.CONTEXT_BUILT, context.to_dict(), turn_id=runtime.turn_id)
            request = runtime._provider_request(context)
        try:
            response = runtime._next_provider(request)
        except ProviderUnavailable as exc:
            with runtime._lock:
                if runtime.state in AgentState.TERMINAL:
                    return runtime.result()
                code = getattr(exc, "code", ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value)
                runtime._emit(EventType.PROVIDER_UNAVAILABLE, {"code": code, "detail": str(exc)}, turn_id=runtime.turn_id)
                runtime._finish_error(AgentState.BLOCKED, AgentError(code, str(exc)))
                return runtime.result()
        except TimeoutError as exc:
            with runtime._lock:
                if runtime.state in AgentState.TERMINAL:
                    return runtime.result()
                decision = runtime.recovery.classify(exc, operation="provider.next_action", provider=True)
                runtime._finish_error(AgentState.TIMEOUT, decision.to_error())
                return runtime.result()
        except ProtocolError as exc:
            with runtime._lock:
                if runtime.state in AgentState.TERMINAL:
                    return runtime.result()
                runtime._finish_error(AgentState.BLOCKED, exc.to_error())
                return runtime.result()
        except BaseException as exc:
            with runtime._lock:
                if runtime.state in AgentState.TERMINAL:
                    return runtime.result()
                decision = runtime.recovery.classify(exc, operation="provider.next_action", provider=True)
                runtime._finish_error(AgentState.BLOCKED, decision.to_error())
                return runtime.result()
        with runtime._lock:
            if runtime.state in AgentState.TERMINAL or runtime._interrupt_requested.is_set():
                if runtime.state not in AgentState.TERMINAL:
                    runtime._interrupt()
                return runtime.result()
            try:
                action = runtime._coerce_action(response)
            except ProtocolError as exc:
                runtime._finish_error(AgentState.BLOCKED, exc.to_error())
                return runtime.result()
            runtime._emit(EventType.ACTION_RECEIVED, {"action": action.to_dict(), "provider": getattr(response, "provider", getattr(runtime.provider, "name", "unknown"))}, turn_id=runtime.turn_id, action_id=action.action_id, idempotency_key=action.idempotency)
            policy_error = runtime.broker.validate(action)
            if policy_error is not None:
                runtime._finish_error(AgentState.BLOCKED, policy_error)
                return runtime.result()
            duplicate = runtime._seen_action_digests.get(action.idempotency)
            if duplicate is not None and duplicate != action.digest():
                runtime._finish_error(AgentState.BLOCKED, AgentError(ErrorCode.DUPLICATE_ACTION.value, "action idempotency key was reused for a different action"))
                return runtime.result()
            if duplicate is None:
                budget_error = runtime._reserve_action_budget(action, context)
                if budget_error is not None:
                    runtime._finish_error(AgentState.BLOCKED, budget_error)
                    return runtime.result()
            runtime._seen_action_digests[action.idempotency] = action.digest()
            if action.kind in (ActionKind.FINISH.value, ActionKind.BLOCKED.value):
                runtime.final_action = action
                runtime._checkpoint_data["final_action"] = action.to_dict()
                runtime._transition(AgentState.FINISHED if action.kind == ActionKind.FINISH.value else AgentState.BLOCKED)
                runtime._emit(EventType.TURN_COMPLETED, {"status": runtime.state, "action": action.to_dict()}, turn_id=runtime.turn_id, action_id=action.action_id, idempotency_key=action.idempotency)
                runtime._emit_session_completed()
                runtime._checkpoint("terminal-action")
                return runtime.result()
            if runtime.tool_calls >= runtime.config.max_tool_calls:
                runtime._finish_error(AgentState.BLOCKED, AgentError(ErrorCode.BUDGET_EXCEEDED.value, "tool-call budget exhausted"))
                return runtime.result()
            try:
                runtime._transition_for_action(action)
            except ProtocolError as exc:
                runtime._finish_error(AgentState.BLOCKED, exc.to_error())
                return runtime.result()
            runtime._emit(EventType.TOOL_STARTED, {"kind": action.kind}, turn_id=runtime.turn_id, action_id=action.action_id, idempotency_key=action.idempotency)
            # Persist the action boundary immediately before invoking the
            # broker.  A crash after this write is treated as an unknown
            # side effect during restore and is never replayed.
            runtime._in_flight_action = action
            runtime._checkpoint_data["in_flight_action"] = action.to_dict()
            runtime._checkpoint("action-accepted")
        try:
            broker_result = call_with_timeout(
                lambda: runtime.broker.execute(action),
                runtime._boundary_timeout(),
                name="tool-execute",
            )
        except TimeoutError as exc:
            with runtime._lock:
                runtime.tool_calls += 1
                if runtime.state in AgentState.TERMINAL:
                    return runtime.result()
                decision = runtime.recovery.classify(
                    exc,
                    operation="tool.execute",
                    side_effect_started=True,
                )
                # Keep both the runtime and broker in-flight markers in
                # the checkpoint.  The daemon worker may still finish
                # later, but its result is deliberately discarded and the
                # action can never be retried automatically.
                runtime._emit(
                    EventType.UNKNOWN_SIDE_EFFECT,
                    {"timeout_seconds": runtime.config.turn_timeout_seconds},
                    turn_id=runtime.turn_id,
                    action_id=action.action_id,
                    idempotency_key=action.idempotency,
                )
                runtime._finish_error(AgentState.UNKNOWN_SIDE_EFFECT, decision.to_error())
                return runtime.result()
        except BaseException as exc:
            with runtime._lock:
                runtime.tool_calls += 1
                if runtime.state in AgentState.TERMINAL:
                    return runtime.result()
                runtime._clear_in_flight_action()
                decision = runtime.recovery.classify(exc, operation="tool.execute", side_effect_started=True)
                runtime._emit(EventType.UNKNOWN_SIDE_EFFECT, decision.details, turn_id=runtime.turn_id, action_id=action.action_id, idempotency_key=action.idempotency)
                runtime._finish_error(AgentState.UNKNOWN_SIDE_EFFECT, decision.to_error())
                return runtime.result()
        with runtime._lock:
            runtime.tool_calls += 1
            if runtime.state in AgentState.TERMINAL or runtime._interrupt_requested.is_set():
                if runtime.state not in AgentState.TERMINAL:
                    # A completed handler racing an interrupt is still
                    # inside a mutation boundary; the caller cannot rely
                    # on whether the result became durable.
                    decision = runtime.recovery.classify(
                        RuntimeError("tool interrupted"),
                        operation="tool.execute",
                        side_effect_started=True,
                    )
                    runtime._emit(
                        EventType.UNKNOWN_SIDE_EFFECT,
                        decision.details,
                        turn_id=runtime.turn_id,
                        action_id=action.action_id,
                        idempotency_key=action.idempotency,
                    )
                    runtime._finish_error(AgentState.UNKNOWN_SIDE_EFFECT, decision.to_error())
                return runtime.result()
            if broker_result.replayed:
                runtime._emit(EventType.ACTION_REPLAYED, broker_result.to_dict(), turn_id=runtime.turn_id, action_id=action.action_id, idempotency_key=action.idempotency)
            if broker_result.error is not None:
                runtime._emit(EventType.TOOL_FAILED, broker_result.to_dict(), turn_id=runtime.turn_id, action_id=action.action_id, idempotency_key=action.idempotency)
                # A broker policy/schema error is deterministic and safe
                # to expose to the provider for a revision.  A side effect
                # failure is deliberately terminal/unknown.
                if broker_result.unknown_side_effect or (broker_result.result is not None and broker_result.result.side_effect):
                    runtime._clear_in_flight_action()
                    runtime._finish_error(AgentState.UNKNOWN_SIDE_EFFECT, broker_result.error)
                    return runtime.result()
            else:
                runtime._emit(EventType.TOOL_COMPLETED, broker_result.to_dict(), turn_id=runtime.turn_id, action_id=action.action_id, idempotency_key=action.idempotency)
            runtime._clear_in_flight_action()
            runtime._transition_after_tool(action, broker_result)
            runtime._emit(EventType.TURN_COMPLETED, {"status": runtime.state, "tool": broker_result.to_dict()}, turn_id=runtime.turn_id, action_id=action.action_id, idempotency_key=action.idempotency)
            runtime._checkpoint("tool-result")
