"""Derive runtime action acceptance from authoritative events and bindings."""

from __future__ import annotations










from typing import Any, Iterable, Mapping

from .backend import (
    ProviderRequest,
)



from .protocol import (
    Action,
    ActionKind,
    ErrorCode,
    ProtocolError,
)








def _runtime_action_acceptance(events: Iterable[Any]) -> dict[str, bool]:
    """Derive runtime acceptance from authoritative action-bound events.

    ``action.received`` proves only that a provider response could be coerced
    into an Action.  Policy, budget, transition, and broker checks happen
    afterwards, so an action is accepted only when the runtime records a
    completed tool boundary or a completed terminal action.  A tool failure,
    unknown side effect, or terminal runtime error wins over a later generic
    ``turn.completed`` event for the same turn.
    """

    acceptance: dict[str, bool] = {}
    action_kinds: dict[str, str] = {}
    actions_by_turn: dict[str, list[str]] = {}
    rejected: set[str] = set()
    tool_completed: set[str] = set()

    for event in events:
        event_type = getattr(event, "event_type", None)
        action_id = getattr(event, "action_id", None)
        turn_id = getattr(event, "turn_id", None)
        payload = getattr(event, "payload", {})
        if event_type == "action.received" and isinstance(action_id, str):
            acceptance[action_id] = False
            if isinstance(turn_id, str):
                actions_by_turn.setdefault(turn_id, []).append(action_id)
            raw_action = payload.get("action") if isinstance(payload, Mapping) else None
            if isinstance(raw_action, Mapping) and isinstance(raw_action.get("kind"), str):
                action_kinds[action_id] = raw_action["kind"]
            continue
        if event_type == "tool.completed" and isinstance(action_id, str):
            tool_completed.add(action_id)
            if action_id not in rejected:
                acceptance[action_id] = True
            continue
        if event_type in {"tool.failed", "unknown_side_effect"} and isinstance(action_id, str):
            rejected.add(action_id)
            acceptance[action_id] = False
            continue
        if event_type == "error":
            targets = [action_id] if isinstance(action_id, str) else actions_by_turn.get(str(turn_id), [])
            for target in targets:
                rejected.add(target)
                acceptance[target] = False
            continue
        if event_type == "turn.completed" and isinstance(action_id, str):
            kind = action_kinds.get(action_id)
            if action_id in rejected:
                acceptance[action_id] = False
            elif kind in {ActionKind.FINISH.value, ActionKind.BLOCKED.value}:
                acceptance[action_id] = True
            elif action_id in tool_completed:
                acceptance[action_id] = True
    return acceptance


def _check_action_binding(action: Action, request: ProviderRequest) -> None:
    if action.expected_source_generation != request.source_generation:
        raise ProtocolError(
            ErrorCode.STALE_SOURCE_GENERATION,
            "provider action source generation does not match request",
        )
    if action.template_lock != request.template_lock:
        raise ProtocolError(
            ErrorCode.TEMPLATE_LOCK_MISMATCH,
            "provider action template lock does not match request",
        )

