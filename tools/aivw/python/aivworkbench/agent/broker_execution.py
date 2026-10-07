"""Serialized action execution for the AIVW tool broker."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .context import redact_text
from .protocol import Action, ActionKind, AgentError, ErrorCode, ProtocolError
from .value_codec import thaw
from .broker_contract import BrokerResult, ToolResult
from .broker_validation import _ACTION_TOOL_DEFAULTS, _capability_error, _tool_for_action, _validate_instance, _validate_policy_values, _validate_result_artifacts

def execute(broker, action: Action | Mapping[str, Any]) -> BrokerResult:
    if not isinstance(action, Action):
        action = Action.from_dict(action)
    try:
        broker.policy.validate_action(action, source_generation=broker.source_generation, template_lock=broker.template_lock)
    except ProtocolError as exc:
        return BrokerResult(action, None, error=exc.to_error())
    key = action.idempotency
    with broker._lock:
        action_digest = action.digest()
        prior_action = broker._actions.get(action.action_id)
        if prior_action is not None and prior_action != action_digest:
            return BrokerResult(
                action,
                None,
                error=AgentError(
                    ErrorCode.DUPLICATE_ACTION.value,
                    "action_id was reused for a different action",
                    {"action_id": action.action_id},
                ),
            )
        prior = broker._results.get(key)
        if prior is not None:
            if prior.action.digest() != action.digest():
                return BrokerResult(action, None, error=AgentError(ErrorCode.DUPLICATE_ACTION.value, "idempotency key was reused for different action"))
            return BrokerResult(
                action,
                prior.tool,
                prior.result,
                prior.error,
                replayed=True,
                unknown_side_effect=prior.unknown_side_effect,
            )
        if broker._active:
            return BrokerResult(action, None, error=AgentError(ErrorCode.INVALID_STATE.value, "another domain action is already executing"))
        broker._active = True
        broker._in_flight = {
            "action": action.to_dict(),
            "idempotency_key": key,
            "started": True,
        }
    result: BrokerResult
    try:
        result = broker._execute_single(action)
    except BaseException as exc:
        # Once the single-flight boundary has been entered, a crash can
        # no longer prove that the domain handler did not mutate state.
        # Convert even unusual BaseException subclasses into an explicit
        # unknown-side-effect result so callers never see an unstructured
        # exception or accidentally retry the action.
        result = broker._unknown_side_effect_result(action, exc)
    finally:
        with broker._lock:
            broker._actions[action.action_id] = action.digest()
            broker._results[key] = result
            broker._in_flight = None
            broker._active = False
    return result


def _execute_single(broker, action: Action) -> BrokerResult:
    if action.kind in (ActionKind.FINISH.value, ActionKind.BLOCKED.value):
        # Terminal actions never invoke a tool.  Their payload remains
        # evidence-free until deterministic gates assess it.
        if "tool" in action.params:
            return BrokerResult(
                action,
                None,
                error=AgentError(
                    ErrorCode.TOOL_NOT_ALLOWED.value,
                    "terminal actions cannot select a domain tool",
                ),
            )
        return BrokerResult(action, None, ToolResult("ACCEPTED", {"terminal": action.kind}))
    try:
        tool_name = _tool_for_action(action, broker.policy, broker._tools)
    except ProtocolError as exc:
        return BrokerResult(action, None, error=exc.to_error())
    if not tool_name:
        error = AgentError(ErrorCode.INVALID_ARGUMENTS.value, "action kind has no domain tool", {"kind": action.kind})
        return BrokerResult(action, None, error=error)
    decision = broker.policy.check_tool(tool_name)
    if not decision.allowed:
        return BrokerResult(action, tool_name, error=AgentError(decision.code, decision.reason, decision.details))
    spec = broker._tools.get(tool_name)
    if spec is None:
        return BrokerResult(action, tool_name, error=AgentError(ErrorCode.TOOL_NOT_FOUND.value, "tool is not registered", {"tool": tool_name}))
    capability_error = _capability_error(spec, broker.policy, tool_name)
    if capability_error is not None:
        return BrokerResult(action, tool_name, error=capability_error)
    params = dict(action.params)
    params.pop("tool", None)
    # Validation failures happen before the handler is called and are
    # deterministic.  Keep them separate from handler failures: a
    # handler can raise ``ValueError`` after a mutation just as easily as
    # it can raise ``RuntimeError``.
    try:
        _validate_instance(params, spec.schema, "params")
        _validate_policy_values(params, broker.policy)
    except ProtocolError as exc:
        return BrokerResult(action, tool_name, error=exc.to_error())
    except (TypeError, ValueError, OSError, RecursionError) as exc:
        return BrokerResult(
            action,
            tool_name,
            error=AgentError(
                ErrorCode.TOOL_SCHEMA_INVALID.value,
                redact_text(str(exc)),
                {"tool": tool_name},
            ),
        )

    try:
        raw = spec.handler(params) if spec.handler is not None else None
        if not isinstance(raw, ToolResult):
            raise TypeError("tool handler must return ToolResult")
        if raw.side_effect and not spec.side_effect:
            # A read-only registration must never be able to become a
            # mutation boundary at runtime.  Since the handler already
            # ran, classify this as unknown side effect and require human
            # recovery instead of allowing a retry.
            unknown = ToolResult(
                "UNKNOWN_SIDE_EFFECT",
                {"tool": tool_name, "error_type": "undeclared_side_effect"},
                {},
                (),
                broker.source_generation,
                True,
                None,
            )
            return BrokerResult(
                action,
                tool_name,
                unknown,
                AgentError(
                    ErrorCode.UNKNOWN_SIDE_EFFECT.value,
                    "tool reported an undeclared side effect",
                    {"tool": tool_name},
                ),
                unknown_side_effect=True,
            )
        _validate_result_artifacts(raw.artifacts, broker.policy)
        json.dumps(raw.to_dict(), ensure_ascii=True, allow_nan=False)
        # A tool may report a gate result as evidence, but the broker does
        # not transform it into an agent verdict.
        normalized = ToolResult(
            raw.status,
            thaw(raw.summary),
            thaw(raw.outputs),
            tuple(thaw(item) for item in raw.artifacts),
            raw.source_generation or broker.source_generation,
            bool(raw.side_effect or spec.side_effect),
            raw.deterministic_gate,
        )
        return BrokerResult(action, tool_name, normalized)
    except ProtocolError as exc:
        # A malformed result is rejected at the boundary.  The handler
        # has already run, so retain an explicit unknown-side-effect
        # marker while preserving the specific validation code.
        invalid = ToolResult(
            "INVALID_RESULT",
            {"tool": tool_name, "error_type": type(exc).__name__},
            {},
            (),
            broker.source_generation,
            True,
            None,
        )
        return BrokerResult(
            action,
            tool_name,
            invalid,
            AgentError(
                ErrorCode.UNKNOWN_SIDE_EFFECT.value,
                "tool returned an invalid result after execution",
                {
                    "tool": tool_name,
                    "validation_code": exc.code,
                    "validation_message": exc.message,
                },
            ),
            unknown_side_effect=True,
        )
    except BaseException as exc:
        return broker._unknown_side_effect_result(action, exc, tool_name=tool_name)


def _unknown_side_effect_result(
    broker,
    action: Action,
    exc: BaseException,
    *,
    tool_name: str | None = None,
) -> BrokerResult:
    """Normalize an exception after entering a domain action boundary."""

    if tool_name is None:
        requested = action.params.get("tool") if isinstance(action.params, Mapping) else None
        tool_name = str(requested) if requested is not None else _ACTION_TOOL_DEFAULTS.get(action.kind)
    safe_type = type(exc).__name__
    unknown = ToolResult(
        "UNKNOWN_SIDE_EFFECT",
        {"tool": tool_name, "error_type": safe_type},
        {},
        (),
        broker.source_generation,
        True,
        None,
    )
    return BrokerResult(
        action,
        tool_name,
        result=unknown,
        error=AgentError(
            ErrorCode.UNKNOWN_SIDE_EFFECT.value,
            "domain action failed with an unknown side effect (%s)" % safe_type,
            {"tool": tool_name, "error_type": safe_type, "detail": redact_text(str(exc))},
        ),
        unknown_side_effect=True,
    )
