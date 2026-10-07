"""State transitions, context requests and action budgets for AIVW runtime."""

from __future__ import annotations

from typing import Any, Mapping

from .backend import ProviderRequest
from .context import BoundedContext, build_context, redact_secrets
from .tool_broker import BrokerResult
from .protocol import Action, ActionKind, AgentError, ErrorCode, ProtocolError
from .runtime_contract import AgentState, _CUMULATIVE_ACTION_BUDGET_FIELDS

def _build_context(runtime, initial: Mapping[str, Any] | None = None) -> BoundedContext:
    fragments = list(runtime.context_fragments)
    if initial is not None:
        # ``initial`` is retained for compatibility with internal calls;
        # public callers should use ``run(initial_context=...)``.
        fragments.append(initial)
    elif runtime._initial_context is not None:
        fragments.append(runtime._initial_context)
    fragments.append(
        {
            "run_id": runtime.run_id,
            "state": runtime.state,
            "source_generation": runtime.source_generation,
            "template_lock": runtime.template_lock,
            "turns_used": runtime.turns,
            "tool_calls_used": runtime.tool_calls,
            "metadata": redact_secrets(runtime.metadata),
            "initial_context": redact_secrets(runtime._initial_context or {}),
            "deterministic_gate": runtime._checkpoint_data.get("deterministic_gate"),
            "last_tool_result": runtime._checkpoint_data.get("last_tool_result"),
        }
    )
    return build_context(fragments, max_bytes=runtime.config.max_context_bytes, max_items=runtime.config.max_context_items)


def _provider_request(runtime, context: BoundedContext) -> ProviderRequest:
    return ProviderRequest(
        run_id=runtime.run_id,
        turn_id=runtime.turn_id or "%s-turn-0000" % runtime.run_id,
        source_generation=runtime.source_generation,
        context=context.value,
        template_lock=runtime.template_lock,
        budget={
            "turns_remaining": max(0, runtime.config.max_turns - runtime.turns),
            "tool_calls_remaining": max(0, runtime.config.max_tool_calls - runtime.tool_calls),
            "context_bytes": context.bytes_used,
        },
        previous_result=runtime._checkpoint_data.get("last_tool_result"),
        metadata=redact_secrets(runtime.metadata),
    )


def _transition_for_action(runtime, action: Action) -> None:
    target = {
        ActionKind.PLAN_EXPERIMENT.value: AgentState.EXPERIMENT_PLAN,
        ActionKind.SUBMIT_EXPERIMENT.value: AgentState.SPECTRE_EXPERIMENT,
        ActionKind.PROPOSE_MODEL.value: AgentState.GENERATE_CANDIDATE,
        ActionKind.REQUEST_REVISION.value: AgentState.REFINE,
    }.get(action.kind)
    if target is not None:
        runtime._transition(target)


def _reserve_action_budget(
    runtime,
    action: Action,
    context: BoundedContext,
) -> AgentError | None:
    """Validate and reserve provider-declared work before side effects."""

    budget = action.budget
    missing = sorted(runtime.config.required_action_budget_fields - set(budget))
    if missing:
        return AgentError(
            ErrorCode.BUDGET_EXCEEDED.value,
            "action omits required budget fields",
            {"fields": missing, "action_id": action.action_id},
        )
    limits: dict[str, float | int | None] = {
        "tokens": runtime.config.max_action_tokens,
        "simulation_cases": runtime.config.max_action_simulation_cases,
        "simulation_seconds": runtime.config.max_action_simulation_seconds,
        "context_bytes": runtime.config.max_context_bytes,
        "wall_seconds": runtime.config.turn_timeout_seconds,
        "turns": max(0, runtime.config.max_turns - runtime.turns + 1),
        "tool_calls": 0
        if action.kind in (ActionKind.FINISH.value, ActionKind.BLOCKED.value)
        else max(0, runtime.config.max_tool_calls - runtime.tool_calls),
    }
    for field_name, limit in limits.items():
        raw = budget.get(field_name)
        if raw is None or limit is None:
            continue
        if float(raw) > float(limit):
            return AgentError(
                ErrorCode.BUDGET_EXCEEDED.value,
                "action budget exceeds runtime limit",
                {
                    "field": field_name,
                    "declared": raw,
                    "limit": limit,
                    "action_id": action.action_id,
                },
            )
    declared_context = budget.get("context_bytes")
    if declared_context is not None and float(declared_context) < float(context.bytes_used):
        return AgentError(
            ErrorCode.BUDGET_EXCEEDED.value,
            "action context budget is smaller than delivered context",
            {
                "declared": declared_context,
                "delivered": context.bytes_used,
                "action_id": action.action_id,
            },
        )
    totals: dict[str, float | int | None] = {
        "tokens": runtime.config.max_total_action_tokens,
        "simulation_cases": runtime.config.max_total_simulation_cases,
        "simulation_seconds": runtime.config.max_total_simulation_seconds,
    }
    for field_name in _CUMULATIVE_ACTION_BUDGET_FIELDS:
        declared = budget.get(field_name, 0)
        total_limit = totals[field_name]
        if total_limit is not None and float(runtime._action_budget_used[field_name]) + float(declared) > float(total_limit):
            return AgentError(
                ErrorCode.BUDGET_EXCEEDED.value,
                "action budget exceeds total runtime limit",
                {
                    "field": field_name,
                    "used": runtime._action_budget_used[field_name],
                    "declared": declared,
                    "limit": total_limit,
                    "action_id": action.action_id,
                },
            )
    for field_name in _CUMULATIVE_ACTION_BUDGET_FIELDS:
        declared = budget.get(field_name, 0)
        if field_name == "simulation_seconds":
            runtime._action_budget_used[field_name] = float(runtime._action_budget_used[field_name]) + float(declared)
        else:
            runtime._action_budget_used[field_name] = int(runtime._action_budget_used[field_name]) + int(declared)
    return None


def _transition_after_tool(runtime, action: Action, result: BrokerResult) -> None:
    if result.error is not None:
        runtime._checkpoint_data["last_tool_result"] = result.to_dict()
        # Keep the current state so a provider can issue a revision; the
        # next turn is still bounded and the error is visible in context.
        return
    runtime._checkpoint_data["last_tool_result"] = result.to_dict()
    next_state = {
        ActionKind.PLAN_EXPERIMENT.value: AgentState.MODEL_HYPOTHESIS,
        ActionKind.SUBMIT_EXPERIMENT.value: AgentState.MEASURE,
        ActionKind.PROPOSE_MODEL.value: AgentState.STATIC_CHECK,
        ActionKind.REQUEST_REVISION.value: AgentState.EXPERIMENT_PLAN,
    }.get(action.kind)
    if next_state is not None:
        runtime._transition(next_state)


def _transition(runtime, target: str) -> None:
    if target == runtime.state:
        return
    allowed = runtime._STATE_TRANSITIONS.get(runtime.state, set())
    # INIT is allowed to enter the normal path.  A resumed runtime may
    # restore a state whose predecessor is not represented in this turn.
    # Terminal actions are legal from every active state.  Providers may
    # also begin with a direct model proposal in an offline M0 run; the
    # semantic target is still bounded by ``_transition_for_action``.
    if target not in allowed and target not in {AgentState.FINISHED, AgentState.BLOCKED}:
        if not (
            runtime.state == AgentState.FINISHED
            and target in {AgentState.QUALIFIED, AgentState.ANALOG_ISLAND, AgentState.BLOCKED}
        ):
            raise ProtocolError(ErrorCode.INVALID_STATE, "invalid agent state transition", {"from": runtime.state, "to": target})
    runtime.state = target
