"""Checkpoint and event-log persistence for the AIVW runtime."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

from .backend import ProviderSession, ProviderUnavailable
from .protocol import AgentError, ErrorCode, Action, Event, ProtocolError, decode_jsonl
from .runtime_contract import AgentState, _CUMULATIVE_ACTION_BUDGET_FIELDS
from .context import redact_secrets

def _checkpoint(runtime, reason: str) -> None:
    try:
        provider_state = dict(runtime.provider.export_state())
    except AttributeError:
        provider_state = {}
    except BaseException as exc:
        raise ProtocolError(
            ErrorCode.CHECKPOINT_INVALID,
            "provider state cannot be checkpointed",
            {"provider": getattr(runtime.provider, "name", type(runtime.provider).__name__), "error_type": type(exc).__name__},
        ) from exc
    safe_provider_state = redact_secrets(provider_state)
    try:
        json.dumps(safe_provider_state, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "provider checkpoint state is not JSON-safe", {"detail": str(exc)}) from exc
    broker_state = runtime.broker.export_state()
    # Exported broker state is untrusted from the runtime's perspective:
    # a custom broker implementation may return arbitrary mappings.  Run
    # it through JSON validation and secret redaction before placing it in
    # a checkpoint or exposing it to a provider.
    safe_broker_state = redact_secrets(broker_state)
    try:
        json.dumps(safe_broker_state, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(
            ErrorCode.CHECKPOINT_INVALID,
            "broker checkpoint state is not JSON-safe",
            {"detail": str(exc)},
        ) from exc
    runtime._checkpoint_data.update(
        {
            "protocol_version": "aivw-agent-v1",
            "run_id": runtime.run_id,
            "state": runtime.state,
            "source_generation": runtime.source_generation,
            "template_lock": runtime.template_lock,
            "turns": runtime.turns,
            "tool_calls": runtime.tool_calls,
            "sequence": runtime.sequence,
            "turn_id": runtime.turn_id,
            "session": None if runtime.session is None else {"session_id": runtime.session.session_id, "provider": runtime.session.provider, "protocol_version": runtime.session.protocol_version, "metadata": redact_secrets(runtime.session.metadata)},
            "initial_context": redact_secrets(runtime._initial_context or {}),
            "provider_state": safe_provider_state,
            "seen_actions": dict(runtime._seen_action_digests),
            "broker_state": safe_broker_state,
            "in_flight_action": None if runtime._in_flight_action is None else runtime._in_flight_action.to_dict(),
            "deterministic_gate": runtime._checkpoint_data.get("deterministic_gate"),
            "action_budget_used": dict(runtime._action_budget_used),
            "last_error": None if runtime.last_error is None else runtime.last_error.to_dict(),
            "session_completed": runtime._session_completed_state is not None,
            "session_completed_state": runtime._session_completed_state,
            "reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    target = runtime.config.checkpoint_path
    if target is None:
        return
    target = Path(target)
    if os.path.lexists(str(target)) and (target.is_symlink() or not target.is_file()):
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint target is not a regular file")
    _prepare_private_parent(target, "checkpoint")
    temporary = target.with_name(".%s.%s.tmp" % (target.name, os.getpid()))
    encoded = json.dumps(runtime._checkpoint_data, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n"
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(str(temporary), flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(target))
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "cannot write checkpoint", {"detail": str(exc)}) from exc


def _load_existing_checkpoint(runtime) -> None:
    target = runtime.config.checkpoint_path
    if target is None or not os.path.lexists(str(target)):
        return
    path = Path(target)
    if path.is_symlink() or not path.is_file():
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "existing checkpoint is not a regular file")
    try:
        value = _load_strict_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "existing checkpoint is invalid", {"detail": str(exc)}) from exc
    if not isinstance(value, Mapping):
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "existing checkpoint must be an object")
    _validate_checkpoint(value)
    if str(value["run_id"]) != runtime.run_id:
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint run_id does not match runtime")
    if str(value["source_generation"]) != runtime.source_generation:
        raise ProtocolError(ErrorCode.STALE_SOURCE_GENERATION, "checkpoint source generation does not match runtime")
    if value.get("template_lock") != runtime.template_lock:
        raise ProtocolError(ErrorCode.TEMPLATE_LOCK_MISMATCH, "checkpoint template lock does not match runtime")
    runtime._restore_checkpoint(value)


def _load_existing_events(runtime) -> None:
    """Restore the append-only event tail without replaying handlers."""
    target = runtime.config.events_path
    if target is None or not os.path.lexists(str(target)):
        return
    path = Path(target)
    if path.is_symlink() or not path.is_file():
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "existing event log is not a regular file")
    try:
        records = decode_jsonl(path.read_text(encoding="utf-8").splitlines(), expected="event")
    except (OSError, ProtocolError) as exc:
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "existing event log is invalid", {"detail": str(exc)}) from exc
    previous = 0
    restored: list[Event] = []
    for event in records:
        if not isinstance(event, Event) or event.run_id != runtime.run_id:
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "event log run_id does not match runtime")
        if event.sequence <= previous:
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "event log sequence is not strictly increasing")
        previous = event.sequence
        restored.append(event)
    if restored:
        checkpoint_sequence = runtime.sequence
        if checkpoint_sequence and previous > checkpoint_sequence:
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "event log is newer than checkpoint")
        runtime.events = restored
        runtime.sequence = max(runtime.sequence, previous)


def _restore_checkpoint(runtime, value: Mapping[str, Any]) -> None:
    runtime.state = str(value.get("state", AgentState.INIT))
    runtime.turns = int(value.get("turns", 0))
    runtime.tool_calls = int(value.get("tool_calls", 0))
    runtime.sequence = int(value.get("sequence", 0))
    if runtime.turns > runtime.config.max_turns or runtime.tool_calls > runtime.config.max_tool_calls:
        raise ProtocolError(ErrorCode.BUDGET_EXCEEDED, "checkpoint exceeds configured runtime budget")
    if runtime.sequence < runtime.turns or runtime.tool_calls > runtime.turns:
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint counters are inconsistent")
    runtime.turn_id = value.get("turn_id") if isinstance(value.get("turn_id"), str) else None
    runtime._seen_action_digests = {str(k): str(v) for k, v in dict(value.get("seen_actions", {})).items()}
    raw_error = value.get("last_error")
    runtime.last_error = None if raw_error is None else AgentError.from_dict(raw_error)
    # Checkpoints are untrusted input.  Keep the persisted object for
    # provenance, but deep-redact all provider/session-facing fields before
    # exposing them through ``result`` or the next request.
    runtime._checkpoint_data = redact_secrets(dict(value))
    raw_initial = value.get("initial_context")
    if raw_initial is not None:
        if not isinstance(raw_initial, Mapping):
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint initial_context must be an object")
        runtime._initial_context = dict(raw_initial)
    raw_final = value.get("final_action")
    if isinstance(raw_final, Mapping):
        runtime.final_action = Action.from_dict(raw_final)
    raw_completed_state = value.get("session_completed_state")
    if isinstance(raw_completed_state, str):
        runtime._session_completed_state = raw_completed_state
    elif bool(value.get("session_completed", False)):
        runtime._session_completed_state = runtime.state
    raw_in_flight = value.get("in_flight_action")
    if raw_in_flight is not None:
        if not isinstance(raw_in_flight, Mapping):
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint in_flight_action must be an object")
        runtime._in_flight_action = Action.from_dict(raw_in_flight)
        runtime._pending_restore_unknown = runtime._in_flight_action
    raw_gate = value.get("deterministic_gate")
    if isinstance(raw_gate, Mapping) and isinstance(raw_gate.get("status"), str):
        runtime._deterministic_gate_status = str(raw_gate.get("status")).upper()
    runtime._action_budget_used = _restore_action_budget_used(value.get("action_budget_used", {}))
    for field_name, total_limit in (
        ("tokens", runtime.config.max_total_action_tokens),
        ("simulation_cases", runtime.config.max_total_simulation_cases),
        ("simulation_seconds", runtime.config.max_total_simulation_seconds),
    ):
        if total_limit is not None and float(runtime._action_budget_used[field_name]) > float(total_limit):
            raise ProtocolError(
                ErrorCode.BUDGET_EXCEEDED,
                "checkpoint action budget exceeds configured runtime limit",
                {"field": field_name, "used": runtime._action_budget_used[field_name], "limit": total_limit},
            )
    broker_state = value.get("broker_state")
    if isinstance(broker_state, Mapping):
        broker_flight = broker_state.get("in_flight")
        if raw_in_flight is not None and isinstance(broker_flight, Mapping):
            broker_action = broker_flight.get("action")
            if not isinstance(broker_action, Mapping):
                raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker in-flight marker has no action")
            broker_action_obj = Action.from_dict(broker_action)
            if broker_action_obj.digest() != runtime._in_flight_action.digest():
                raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "runtime and broker in-flight actions disagree")
        elif raw_in_flight is None and broker_flight is not None:
            # The broker marker is independently persisted.  Treat a
            # marker without the runtime marker as a crash boundary, but
            # keep the action for the explicit UNKNOWN_SIDE_EFFECT event.
            if not isinstance(broker_flight, Mapping) or not isinstance(broker_flight.get("action"), Mapping):
                raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "broker in-flight marker is invalid")
        try:
            runtime.broker.restore_state(broker_state)
        except ProtocolError as exc:
            if exc.code != ErrorCode.UNKNOWN_SIDE_EFFECT.value:
                raise
            raw_broker_flight = broker_state.get("in_flight")
            if runtime._pending_restore_unknown is None and isinstance(raw_broker_flight, Mapping):
                raw_action = raw_broker_flight.get("action")
                if isinstance(raw_action, Mapping):
                    runtime._pending_restore_unknown = Action.from_dict(raw_action)
            if runtime._pending_restore_unknown is None:
                raise
    raw_session = value.get("session")
    if isinstance(raw_session, Mapping):
        runtime.session = ProviderSession(str(raw_session["session_id"]), str(raw_session["provider"]), str(raw_session.get("protocol_version", "aivw-agent-v1")), dict(raw_session.get("metadata", {})))
        runtime._provider_session_ready = False
    raw_provider_state = value.get("provider_state", {})
    if raw_provider_state is not None:
        if not isinstance(raw_provider_state, Mapping):
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint provider_state must be an object")
        try:
            runtime.provider.restore_state(raw_provider_state)
        except AttributeError:
            if raw_provider_state:
                raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "provider cannot restore checkpoint state")
        except ProviderUnavailable as exc:
            # Preserve checkpoint/contract errors.  Only an actual
            # unavailable/disconnected provider is normalized to the
            # generic model-unavailable boundary; otherwise callers lose
            # the distinction between a forged checkpoint and a network
            # outage.
            provider_code = getattr(exc, "code", "")
            if provider_code in {
                ErrorCode.CHECKPOINT_INVALID.value,
                ErrorCode.BUNDLE_HASH_MISMATCH.value,
                ErrorCode.BUNDLE_PROTOCOL_MISMATCH.value,
                ErrorCode.BUNDLE_SOURCE_MISMATCH.value,
                ErrorCode.BUNDLE_TEMPLATE_MISMATCH.value,
            }:
                raise ProtocolError(
                    provider_code,
                    str(exc),
                    {"phase": "checkpoint_restore"},
                ) from exc
            # A provider cursor that cannot be restored for availability
            # reasons is a provider boundary, not a malformed AIVW
            # checkpoint.  The caller can select an explicit fallback
            # while preserving the original checkpoint for audit.
            raise ProtocolError(
                ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
                str(exc),
                {"code": provider_code, "phase": "checkpoint_restore"},
            ) from exc


def _validate_checkpoint(value: Mapping[str, Any]) -> None:
    required = {"protocol_version", "run_id", "state", "source_generation", "turns", "tool_calls", "sequence", "seen_actions"}
    missing = sorted(required - set(value))
    if missing:
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint misses required fields", {"fields": missing})
    if value.get("protocol_version") != "aivw-agent-v1":
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint protocol version is unsupported")
    allowed_top = {
        "protocol_version", "run_id", "state", "source_generation", "template_lock", "turns", "tool_calls", "sequence",
        "turn_id", "session", "provider_state", "seen_actions", "broker_state", "in_flight_action", "deterministic_gate",
        "last_tool_result", "last_error", "final_action", "session_completed", "session_completed_state", "resume_state", "reason", "updated_at", "initial_context", "action_budget_used",
    }
    unknown_top = sorted(str(key) for key in value if key not in allowed_top)
    if unknown_top:
        raise ProtocolError(ErrorCode.UNKNOWN_FIELD, "checkpoint contains unknown fields", {"fields": unknown_top})
    if not isinstance(value.get("run_id"), str) or not value.get("run_id"):
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint run_id is invalid")
    if value.get("state") not in {
        getattr(AgentState, name)
        for name in dir(AgentState)
        if name.isupper() and isinstance(getattr(AgentState, name), str)
    }:
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint state is invalid")
    for field_name in ("turns", "tool_calls", "sequence"):
        raw = value.get(field_name)
        if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint %s is invalid" % field_name)
    if not isinstance(value.get("seen_actions"), Mapping):
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint seen_actions must be an object")
    for key, digest in value.get("seen_actions", {}).items():
        if not isinstance(key, str) or not isinstance(digest, str) or len(digest) != 64:
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint seen_actions entry is invalid")
        try:
            int(digest, 16)
        except ValueError as exc:
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint action digest is invalid") from exc
    if value.get("turn_id") is not None and not isinstance(value.get("turn_id"), str):
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint turn_id is invalid")
    if value.get("template_lock") is not None and not isinstance(value.get("template_lock"), str):
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint template_lock is invalid")
    if value.get("session_completed") is not None and not isinstance(value.get("session_completed"), bool):
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint session_completed is invalid")
    if "action_budget_used" in value:
        _restore_action_budget_used(value.get("action_budget_used"))
    broker_state = value.get("broker_state")
    if broker_state is not None:
        if not isinstance(broker_state, Mapping):
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint broker_state must be an object")
        for key in ("actions", "results"):
            if key in broker_state and not isinstance(broker_state[key], Mapping):
                raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint broker_state.%s must be an object" % key)
        if "in_flight" in broker_state and broker_state["in_flight"] is not None and not isinstance(broker_state["in_flight"], Mapping):
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint broker_state.in_flight is invalid")
    session = value.get("session")
    if session is not None:
        if not isinstance(session, Mapping):
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint session is invalid")
        required_session = {"session_id", "provider", "protocol_version", "metadata"}
        if not required_session.issubset(session) or session.get("protocol_version") != "aivw-agent-v1":
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint provider session is invalid")
        if any(not isinstance(session.get(name), str) or not session.get(name) for name in ("session_id", "provider")):
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint provider session identity is invalid")
        if not isinstance(session.get("metadata"), Mapping):
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint provider session metadata is invalid")
    try:
        json.dumps(dict(value), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint contains non-JSON data", {"detail": str(exc)}) from exc


def _restore_action_budget_used(value: object) -> dict[str, float | int]:
    """Validate the cumulative provider-budget ledger from a checkpoint."""

    if not isinstance(value, Mapping):
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint action_budget_used must be an object")
    allowed = _CUMULATIVE_ACTION_BUDGET_FIELDS
    unknown = [key for key in value if not isinstance(key, str) or key not in allowed]
    if unknown:
        raise ProtocolError(
            ErrorCode.UNKNOWN_FIELD,
            "checkpoint action_budget_used contains unknown fields",
            {"fields": sorted(str(item) for item in unknown)},
        )
    result: dict[str, float | int] = {
        "tokens": 0,
        "simulation_cases": 0,
        "simulation_seconds": 0.0,
    }
    for field_name in allowed:
        raw = value.get(field_name, 0)
        if (
            not isinstance(raw, (int, float))
            or isinstance(raw, bool)
            or not math.isfinite(float(raw))
            or float(raw) < 0
        ):
            raise ProtocolError(
                ErrorCode.CHECKPOINT_INVALID,
                "checkpoint action budget value is invalid",
                {"field": field_name},
            )
        if field_name == "simulation_seconds":
            result[field_name] = float(raw)
        elif not float(raw).is_integer():
            raise ProtocolError(
                ErrorCode.CHECKPOINT_INVALID,
                "checkpoint integer action budget value is invalid",
                {"field": field_name},
            )
        else:
            result[field_name] = int(raw)
    return result


def _prepare_private_parent(target: Path, label: str) -> None:
    """Create a private persistence parent and reject symlink components."""
    parent = target.parent
    current = Path(parent.anchor) if parent.is_absolute() else Path.cwd()
    parts = parent.parts[1:] if parent.is_absolute() else parent.parts
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "%s parent traverses a symlink" % label)
        if current.exists() and not current.is_dir():
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "%s parent is not a directory" % label)
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(str(parent), 0o700)
    except OSError as exc:
        raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "cannot prepare %s parent" % label, {"detail": str(exc)}) from exc


class _DuplicateCheckpointKey(ValueError):
    """Internal marker for duplicate JSON object names in checkpoints."""


def _reject_checkpoint_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant: %s" % value)


def _reject_checkpoint_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise _DuplicateCheckpointKey("duplicate checkpoint field: %s" % key)
        result[key] = item
    return result


def _load_strict_json(raw: str | bytes) -> Any:
    """Decode persisted runtime JSON without last-wins or NaN ambiguity."""
    try:
        return json.loads(
            raw,
            parse_constant=_reject_checkpoint_constant,
            object_pairs_hook=_reject_checkpoint_duplicates,
        )
    except (TypeError, ValueError, UnicodeError, _DuplicateCheckpointKey) as exc:
        raise ValueError("invalid checkpoint JSON: %s" % exc) from exc
