"""Checkpoint restore and idempotency state for the AIVW tool broker."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .protocol import Action, ActionKind, AgentError, ErrorCode, ProtocolError
from .broker_contract import BrokerResult, ToolResult
from .broker_validation import _ACTION_TOOL_DEFAULTS, _strict_checkpoint_keys

def restore_state(broker, value: Mapping[str, Any]) -> None:
    """Restore completed idempotent results without invoking handlers."""
    if not isinstance(value, Mapping):
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint must be an object")
    _strict_checkpoint_keys(value, {"actions", "results", "in_flight"}, "broker checkpoint")
    actions = value.get("actions", {})
    results = value.get("results", {})
    if not isinstance(actions, Mapping) or not isinstance(results, Mapping):
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint has invalid maps")
    in_flight = value.get("in_flight")
    if in_flight is not None:
        if not isinstance(in_flight, Mapping):
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint has invalid in-flight action")
        _strict_checkpoint_keys(in_flight, {"action", "idempotency_key", "started"}, "broker checkpoint in-flight")
        if not isinstance(in_flight.get("action"), Mapping) or in_flight.get("started") is not True:
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint has invalid in-flight action")
        pending = Action.from_dict(in_flight["action"])
        if in_flight.get("idempotency_key") != pending.idempotency:
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker in-flight idempotency key does not match action")
        # A crash while a broker call was in progress cannot prove whether
        # the handler mutated its domain.  Never replay it automatically.
        raise ProtocolError(
            ErrorCode.UNKNOWN_SIDE_EFFECT,
            "checkpoint contains an action with unknown side effects",
            {"action_id": pending.action_id, "idempotency_key": pending.idempotency},
        )
    restored_actions: dict[str, str] = {}
    for key, digest in actions.items():
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint action map is invalid")
        restored_actions[key] = digest
    restored_results: dict[str, BrokerResult] = {}
    restored_action_ids: set[str] = set()
    for key, raw in results.items():
        if not isinstance(key, str) or not isinstance(raw, Mapping):
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint result map is invalid")
        _strict_checkpoint_keys(
            raw,
            {"action", "action_id", "tool", "result", "error", "replayed", "unknown_side_effect"},
            "broker checkpoint result",
        )
        raw_action = raw.get("action")
        if not isinstance(raw_action, Mapping):
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint result has no complete action")
        action = Action.from_dict(raw_action)
        raw_result = raw.get("result")
        result = None
        if raw_result is not None:
            if not isinstance(raw_result, Mapping):
                raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint tool result is invalid")
            _strict_checkpoint_keys(
                raw_result,
                {"status", "summary", "outputs", "artifacts", "source_generation", "side_effect", "deterministic_gate"},
                "broker checkpoint tool result",
            )
            raw_artifacts = raw_result.get("artifacts", [])
            if not isinstance(raw_artifacts, list):
                raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint artifacts are invalid")
            if any(not isinstance(item, Mapping) for item in raw_artifacts):
                raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint artifact entry is invalid")
            result = ToolResult(
                raw_result.get("status"),
                raw_result.get("summary", {}),
                raw_result.get("outputs", {}),
                tuple(dict(item) for item in raw_artifacts),
                raw_result.get("source_generation"),
                raw_result.get("side_effect", False),
                raw_result.get("deterministic_gate"),
            )
        raw_error = raw.get("error")
        error = None if raw_error is None else AgentError.from_dict(raw_error)
        if raw.get("action_id") != action.action_id:
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint action_id does not match action")
        if key != action.idempotency:
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint idempotency key does not match action")
        if action.action_id in restored_action_ids:
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint contains duplicate action_id")
        restored_action_ids.add(action.action_id)
        declared_digest = restored_actions.get(action.action_id)
        if declared_digest is None or declared_digest != action.digest():
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint action digest does not match action")
        tool = raw.get("tool")
        if tool is not None and (not isinstance(tool, str) or not tool):
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint tool name is invalid")
        replayed = raw.get("replayed", False)
        unknown_side_effect = raw.get("unknown_side_effect", False)
        if not isinstance(replayed, bool) or not isinstance(unknown_side_effect, bool):
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint result flags are invalid")
        # A persisted result is a replay boundary.  Its shape must be
        # unambiguous: successful results have a result and no error;
        # deterministic failures have an error and no side-effect flag;
        # unknown-side-effect failures have both an error and a marked
        # result.  Rejecting contradictory combinations prevents a forged
        # checkpoint from being replayed as a successful action.
        if error is None:
            if result is None:
                raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint result has neither result nor error")
            if unknown_side_effect:
                raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "unknown-side-effect result must include an error")
        else:
            if result is not None:
                if not unknown_side_effect or not result.side_effect:
                    raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "side-effect error must be marked unknown_side_effect")
            elif unknown_side_effect:
                raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "unknown-side-effect result must include a tool result")
        expected_tool = _ACTION_TOOL_DEFAULTS.get(action.kind)
        if action.kind in (ActionKind.FINISH.value, ActionKind.BLOCKED.value):
            if tool is not None:
                raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "terminal broker result cannot name a tool")
        elif expected_tool is None or tool != expected_tool:
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint tool does not own action kind")
        elif expected_tool not in broker._tools:
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint action owner is not registered", {"tool": expected_tool})
        if result is not None:
            if result.source_generation not in (None, broker.source_generation):
                raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint result source generation is stale")
            if result.side_effect and error is None and not bool(broker._tools.get(tool).side_effect if tool in broker._tools else False):
                # A persisted side-effect flag is meaningful only when the
                # owning tool declared that capability.  This prevents a
                # forged checkpoint from turning a read-only handler into
                # an unknown mutation boundary.
                raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint side-effect flag is not owned by tool")
        if replayed:
            # ``replayed`` is an execution-time annotation.  Checkpoints
            # persist the original result, so accepting it here would
            # make a restored record appear to have been replayed twice.
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint result cannot be marked replayed")
        restored_results[key] = BrokerResult(
            action,
            tool,
            result,
            error,
            replayed,
            unknown_side_effect,
        )
    # Every restored action must have exactly one result entry.  This
    # prevents a forged action digest from silently becoming a replayable
    # idempotency record.
    if set(restored_actions) != restored_action_ids or set(restored_results) != {item.action.idempotency for item in restored_results.values()}:
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker checkpoint actions and results disagree")
    with broker._lock:
        broker._actions = restored_actions
        broker._results = restored_results
        broker._in_flight = None
