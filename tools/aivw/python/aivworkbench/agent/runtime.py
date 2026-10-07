"""Finite-state, checkpointable AIVW agent runtime.

The runtime owns AIVW session state and event provenance.  A provider only
proposes an action; a :class:`~aivworkbench.agent.tool_broker.ToolBroker`
performs at most one domain action per turn.  The implementation is
synchronous on purpose so it remains usable on the production Python 3.9.13
image; it does not require an event-loop package.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading
import time
import uuid
from typing import Any, Iterable, Iterator, Mapping, TypeVar

from .backend import AgentProvider, ProviderSession, ProviderUnavailable
from .context import redact_secrets
from .recovery import RecoveryManager
from .protocol import (
    Action,
    AgentError,
    ErrorCode,
    Event,
    EventType,
    ProtocolError,
    decode_jsonl,
    encode_jsonl,
    make_event,
)
from .value_codec import thaw
from .tool_broker import ToolBroker
from . import runtime_loop
from . import runtime_persistence
from . import runtime_policy
from . import runtime_boundary
from .runtime_call import call_with_timeout


from .runtime_contract import (
    AgentState,
    RuntimeConfig,
    RuntimeResult,
)


_CallResult = TypeVar("_CallResult")



class AgentRuntime:
    """A bounded, resumable agent loop.

    ``run`` can be called repeatedly after an interrupt only through
    ``resume``; an in-flight run is protected by a lock.  A checkpoint is
    written after every accepted action and tool result, before the next
    provider request.  A crash therefore either replays an idempotent action
    or resumes at a known boundary; it never silently reruns an unknown
    mutation.
    """

    _STATE_TRANSITIONS = {
        AgentState.INIT: {AgentState.INPUT_QUALIFICATION, AgentState.BLOCKED, AgentState.EXPERIMENT_PLAN, AgentState.GENERATE_CANDIDATE, AgentState.REFINE},
        AgentState.INPUT_QUALIFICATION: {AgentState.STRUCTURE_SNAPSHOT, AgentState.TEMPLATE_MATCH, AgentState.EXPERIMENT_PLAN, AgentState.GENERATE_CANDIDATE, AgentState.REFINE, AgentState.BLOCKED},
        AgentState.STRUCTURE_SNAPSHOT: {AgentState.TEMPLATE_MATCH, AgentState.EXPERIMENT_PLAN, AgentState.GENERATE_CANDIDATE, AgentState.BLOCKED},
        AgentState.TEMPLATE_MATCH: {AgentState.EXPERIMENT_PLAN, AgentState.MODEL_HYPOTHESIS, AgentState.GENERATE_CANDIDATE, AgentState.BLOCKED},
        AgentState.EXPERIMENT_PLAN: {AgentState.SPECTRE_EXPERIMENT, AgentState.MODEL_HYPOTHESIS, AgentState.GENERATE_CANDIDATE, AgentState.BLOCKED},
        AgentState.SPECTRE_EXPERIMENT: {AgentState.MEASURE, AgentState.BLOCKED, AgentState.UNKNOWN_SIDE_EFFECT},
        AgentState.MEASURE: {AgentState.MODEL_HYPOTHESIS, AgentState.BLOCKED},
        AgentState.MODEL_HYPOTHESIS: {AgentState.GENERATE_CANDIDATE, AgentState.EXPERIMENT_PLAN, AgentState.BLOCKED},
        AgentState.GENERATE_CANDIDATE: {AgentState.STATIC_CHECK, AgentState.BLOCKED},
        AgentState.STATIC_CHECK: {AgentState.XCELIUM_CHECK, AgentState.REFINE, AgentState.BLOCKED},
        AgentState.XCELIUM_CHECK: {AgentState.CORRELATION, AgentState.REFINE, AgentState.BLOCKED},
        AgentState.CORRELATION: {AgentState.QUALIFIED, AgentState.ANALOG_ISLAND, AgentState.REFINE, AgentState.BLOCKED},
        AgentState.REFINE: {AgentState.EXPERIMENT_PLAN, AgentState.MODEL_HYPOTHESIS, AgentState.BLOCKED},
        AgentState.QUALIFIED: set(),
        AgentState.ANALOG_ISLAND: set(),
        AgentState.FINISHED: set(),
        AgentState.BLOCKED: set(),
        AgentState.INTERRUPTED: {AgentState.INPUT_QUALIFICATION, AgentState.STRUCTURE_SNAPSHOT, AgentState.TEMPLATE_MATCH, AgentState.EXPERIMENT_PLAN, AgentState.MODEL_HYPOTHESIS, AgentState.GENERATE_CANDIDATE, AgentState.REFINE},
        AgentState.TIMEOUT: set(),
        AgentState.UNKNOWN_SIDE_EFFECT: set(),
    }

    def __init__(
        self,
        provider: AgentProvider,
        broker: ToolBroker,
        *,
        run_id: str | None = None,
        source_generation: str,
        template_lock: str | None = None,
        config: RuntimeConfig | None = None,
        context_fragments: Iterable[Mapping[str, Any] | object] = (),
        metadata: Mapping[str, Any] | None = None,
        _load_persisted: bool = True,
    ) -> None:
        self.provider = provider
        self.broker = broker
        self.run_id = run_id or "run-" + uuid.uuid4().hex
        self.source_generation = source_generation
        self.template_lock = template_lock
        self.config = config or RuntimeConfig()
        self.context_fragments = tuple(context_fragments)
        # ``initial_context`` is part of the run contract, rather than a
        # one-shot bootstrap hint.  Keep the bounded/redacted copy on the
        # runtime so every subsequent turn and a checkpoint restore see the
        # same source snapshot.
        self._initial_context: Mapping[str, Any] | None = None
        self.metadata = dict(metadata or {})
        self.state = AgentState.INIT
        self.turns = 0
        self.tool_calls = 0
        self.sequence = 0
        self.turn_id: str | None = None
        self.session: ProviderSession | None = None
        # A restored session is only an identity/provenance record until the
        # current provider instance completes one resume handshake.  This
        # ephemeral flag prevents ``resume().run()`` from invoking
        # ``provider.resume`` twice while still requiring a handshake after a
        # process restart.
        self._provider_session_ready = False
        self.events: list[Event] = []
        self.last_error: AgentError | None = None
        self.final_action: Action | None = None
        self._seen_action_digests: dict[str, str] = {}
        self._checkpoint_data: dict[str, Any] = {}
        self._interrupt_requested = threading.Event()
        self._started_at: float | None = None
        self._lock = threading.RLock()
        self.recovery = RecoveryManager()
        self._deterministic_gate_status: str | None = None
        self._action_budget_used: dict[str, float | int] = {
            "tokens": 0,
            "simulation_cases": 0,
            "simulation_seconds": 0.0,
        }
        # ``in_flight_action`` is persisted before entering the broker.  It is
        # intentionally kept separate from the broker's own marker because a
        # process can die in the small interval between the two calls.
        self._in_flight_action: Action | None = None
        self._session_completed_state: str | None = None
        self._pending_restore_unknown: Action | None = None
        if _load_persisted:
            self._load_existing_checkpoint()
            self._load_existing_events()
            self._flush_pending_restore_unknown()

    def evaluate_gate(self, status: str, *, gate: str = "deterministic", evidence: Mapping[str, Any] | None = None) -> RuntimeResult:
        """Record a deterministic gate result and make it visible to providers.

        Only a caller-owned checker may invoke this method.  The provider's
        ``FINISH`` action never establishes qualification by itself.
        """
        normalized = str(status).upper()
        if normalized not in {"PASS", "FAIL", "BLOCKED", "QUALIFIED", "ANALOG_ISLAND"}:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "invalid deterministic gate status", {"status": status})
        with self._lock:
            self._deterministic_gate_status = normalized
            self._checkpoint_data["deterministic_gate"] = {
                "name": gate,
                "status": normalized,
                "evidence": redact_secrets(dict(evidence or {})),
            }
            self._emit(EventType.GATE_EVALUATED, self._checkpoint_data["deterministic_gate"])
            if normalized == "QUALIFIED":
                # Qualification is an explicit external gate decision.  A
                # provider FINISH leaves the runtime in FINISHED until this
                # method is called; it is never inferred from tool output.
                self._transition(AgentState.QUALIFIED)
            elif normalized == "ANALOG_ISLAND":
                self._transition(AgentState.ANALOG_ISLAND)
            elif normalized in {"FAIL", "BLOCKED"}:
                if self.state not in {AgentState.QUALIFIED, AgentState.ANALOG_ISLAND}:
                    self._transition(AgentState.BLOCKED)
            if self.state in AgentState.TERMINAL:
                self._emit_session_completed()
            self._checkpoint("gate-evaluated")
            return self.result()

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint: str | Path | Mapping[str, Any],
        provider: AgentProvider,
        broker: ToolBroker,
        *,
        config: RuntimeConfig | None = None,
        context_fragments: Iterable[Mapping[str, Any] | object] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> "AgentRuntime":
        if isinstance(checkpoint, Mapping):
            # RuntimeResult exposes a recursively frozen checkpoint.  Thaw it
            # so an in-memory restore has the same JSON-shaped contract as a
            # checkpoint loaded from disk (lists must not remain tuples).
            value = thaw(checkpoint)
        else:
            path = Path(checkpoint)
            try:
                value = runtime_persistence._load_strict_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "cannot read checkpoint", {"detail": str(exc)}) from exc
        if not isinstance(value, Mapping):
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "checkpoint must be an object")
        runtime_persistence._validate_checkpoint(value)
        runtime = cls(
            provider,
            broker,
            run_id=str(value["run_id"]),
            source_generation=str(value["source_generation"]),
            template_lock=value.get("template_lock"),
            config=config,
            context_fragments=context_fragments,
            metadata=metadata,
            _load_persisted=False,
        )
        runtime._restore_checkpoint(value)
        runtime._flush_pending_restore_unknown()
        return runtime

    def run(self, *, initial_context: Mapping[str, Any] | None = None) -> RuntimeResult:
        return runtime_loop.run(self, initial_context=initial_context)

    def interrupt(self) -> RuntimeResult:
        self._interrupt_requested.set()
        try:
            self.provider.interrupt(self.session)
        except Exception:
            pass
        with self._lock:
            if self.state not in AgentState.TERMINAL:
                self._provider_session_ready = False
                if self._in_flight_action is not None:
                    action = self._in_flight_action
                    decision = self.recovery.classify(
                        RuntimeError("tool interrupted"),
                        operation="tool.execute",
                        side_effect_started=True,
                    )
                    self._emit(
                        EventType.UNKNOWN_SIDE_EFFECT,
                        decision.details,
                        turn_id=self.turn_id,
                        action_id=action.action_id,
                        idempotency_key=action.idempotency,
                    )
                    self._finish_error(AgentState.UNKNOWN_SIDE_EFFECT, decision.to_error())
                else:
                    self._interrupt()
            return self.result()

    def resume(self) -> "AgentRuntime":
        with self._lock:
            if self.state not in {AgentState.INTERRUPTED, AgentState.BLOCKED}:
                raise ProtocolError(ErrorCode.INVALID_STATE, "runtime is not resumable", {"state": self.state})
            if self.state == AgentState.BLOCKED and self.last_error and self.last_error.code not in {
                ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value,
                ErrorCode.PROVIDER_UNAVAILABLE.value,
                ErrorCode.PROVIDER_DISCONNECTED.value,
                ErrorCode.PROVIDER_TIMEOUT.value,
                ErrorCode.INTERRUPTED.value,
            }:
                raise ProtocolError(ErrorCode.INVALID_STATE, "runtime is blocked by a non-resumable error", {"code": self.last_error.code})
            self._interrupt_requested.clear()
            # A resumed run receives a fresh total budget.  Paused wall time
            # must not consume the provider handshake deadline.
            self._started_at = time.monotonic()
            if self.session is not None:
                context = self._build_context()
                try:
                    request = self._provider_request(context)
                    self.session = call_with_timeout(
                        lambda: self.provider.resume(self.session, request),
                        self._boundary_timeout(),
                        name="provider-resume",
                    )
                    self._provider_session_ready = True
                except ProviderUnavailable as exc:
                    # Resume is a provider boundary too.  Do not leak a raw
                    # provider exception to callers or pretend that a failed
                    # handshake restored the session.
                    code = getattr(exc, "code", ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value)
                    self._provider_session_ready = False
                    self._finish_error(AgentState.BLOCKED, AgentError(code, str(exc)))
                    return self
                except TimeoutError as exc:
                    self._provider_session_ready = False
                    decision = self.recovery.classify(exc, operation="provider.resume", provider=True)
                    self._finish_error(AgentState.BLOCKED, decision.to_error())
                    return self
                except BaseException as exc:
                    self._provider_session_ready = False
                    decision = self.recovery.classify(exc, operation="provider.resume", provider=True)
                    self._finish_error(AgentState.BLOCKED, decision.to_error())
                    return self
            previous = self.state
            self.state = self._checkpoint_data.get("resume_state", AgentState.MODEL_HYPOTHESIS)
            self.last_error = None
            self._session_completed_state = None
            self._checkpoint_data["session_completed"] = False
            self._checkpoint_data.pop("session_completed_state", None)
            self._emit(EventType.SESSION_RESUMED, {"from_state": previous, "state": self.state})
            self._checkpoint("resume")
            return self

    def result(self) -> RuntimeResult:
        return RuntimeResult(self.run_id, self.state, self._status(), self.turns, self.tool_calls, tuple(self.events), self.last_error, self.final_action, dict(self._checkpoint_data))

    def iter_events(self) -> Iterator[Event]:
        return iter(tuple(self.events))

    def write_events(self, path: str | Path | None = None) -> Path | None:
        target = Path(path) if path is not None else self.config.events_path
        if target is None:
            return None
        if os.path.lexists(str(target)) and (target.is_symlink() or not target.is_file()):
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "event log target is not a regular file")
        runtime_persistence._prepare_private_parent(target, "event log")
        # append-only: existing records are never replaced; duplicate event
        # IDs are skipped when a resumed runtime flushes its in-memory tail.
        existing: set[str] = set()
        if os.path.lexists(str(target)):
            try:
                existing = {event.event_id for event in decode_jsonl(target.read_text(encoding="utf-8").splitlines(), expected="event")}
            except (OSError, ProtocolError):
                raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "existing event log is invalid")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(str(target), flags, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8", newline="") as stream:
                for event in self.events:
                    if event.event_id in existing:
                        continue
                    stream.write(encode_jsonl(event))
                    if self.config.fsync_events:
                        stream.flush()
                        os.fsync(stream.fileno())
                    existing.add(event.event_id)
        except OSError as exc:
            raise ProtocolError(ErrorCode.CHECKPOINT_INVALID, "cannot append event log", {"detail": str(exc)}) from exc
        return target

    def _next_provider(self, *args, **kwargs):
        return runtime_boundary._next_provider(self, *args, **kwargs)

    def _boundary_timeout(self, *args, **kwargs):
        return runtime_boundary._boundary_timeout(self, *args, **kwargs)

    def _coerce_action(self, *args, **kwargs):
        return runtime_boundary._coerce_action(self, *args, **kwargs)

    def _build_context(self, *args, **kwargs):
        return runtime_policy._build_context(self, *args, **kwargs)

    def _provider_request(self, *args, **kwargs):
        return runtime_policy._provider_request(self, *args, **kwargs)

    def _transition_for_action(self, *args, **kwargs):
        return runtime_policy._transition_for_action(self, *args, **kwargs)

    def _reserve_action_budget(self, *args, **kwargs):
        return runtime_policy._reserve_action_budget(self, *args, **kwargs)

    def _transition_after_tool(self, *args, **kwargs):
        return runtime_policy._transition_after_tool(self, *args, **kwargs)

    def _transition(self, *args, **kwargs):
        return runtime_policy._transition(self, *args, **kwargs)

    def _interrupt(self) -> None:
        if self.state in AgentState.TERMINAL:
            return
        self._checkpoint_data["resume_state"] = self.state
        self.state = AgentState.INTERRUPTED
        self.last_error = AgentError(ErrorCode.INTERRUPTED.value, "agent was interrupted")
        self._emit(EventType.INTERRUPTED, {"resume_state": self._checkpoint_data["resume_state"]})
        self._emit_session_completed()
        self._checkpoint("interrupt")

    def _finish_error(self, state: str, error: AgentError) -> None:
        self.state = state
        self.last_error = error
        self._emit(EventType.ERROR, {"error": error.to_dict(), "state": state}, turn_id=self.turn_id)
        self._emit_session_completed()
        self._checkpoint("error")

    def _emit_session_completed(self) -> None:
        """Emit one terminal lifecycle event for this runtime instance."""

        if self._session_completed_state == self.state or self.state not in AgentState.TERMINAL:
            return
        self._emit(
            EventType.SESSION_COMPLETED,
            {
                "state": self.state,
                "status": self._status(),
                "error": None if self.last_error is None else self.last_error.to_dict(),
            },
            turn_id=self.turn_id,
        )
        self._session_completed_state = self.state
        self._checkpoint_data["session_completed"] = True
        self._checkpoint_data["session_completed_state"] = self.state

    def _clear_in_flight_action(self) -> None:
        self._in_flight_action = None
        self._checkpoint_data.pop("in_flight_action", None)

    def _flush_pending_restore_unknown(self) -> None:
        """Materialize a crash recovery event after persisted state is loaded."""

        with self._lock:
            action = self._pending_restore_unknown
            if action is None:
                return
            self._pending_restore_unknown = None
            self._clear_in_flight_action()
            self.state = AgentState.UNKNOWN_SIDE_EFFECT
            self.last_error = AgentError(
                ErrorCode.UNKNOWN_SIDE_EFFECT.value,
                "checkpoint contains an action with unknown side effects",
                {"action_id": action.action_id, "idempotency_key": action.idempotency},
            )
            self._emit(
                EventType.UNKNOWN_SIDE_EFFECT,
                {
                    "operation": "checkpoint.restore",
                    "action_id": action.action_id,
                    "idempotency_key": action.idempotency,
                },
                turn_id=self.turn_id,
                action_id=action.action_id,
                idempotency_key=action.idempotency,
            )
            self._emit_session_completed()
            self._checkpoint("restore-unknown-side-effect")

    def _status(self) -> str:
        if self.state == AgentState.FINISHED:
            return "FINISHED"
        if self.state == AgentState.QUALIFIED:
            return "QUALIFIED"
        if self.state == AgentState.ANALOG_ISLAND:
            return "ANALOG_ISLAND"
        if self.state == AgentState.INTERRUPTED:
            return ErrorCode.INTERRUPTED.value
        if self.last_error is not None:
            return self.last_error.code
        return self.state

    def _emit(
        self,
        event_type: EventType | str,
        payload: Mapping[str, Any],
        *,
        turn_id: str | None = None,
        action_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Event:
        self.sequence += 1
        event = make_event(event_type, run_id=self.run_id, sequence=self.sequence, payload=redact_secrets(payload), turn_id=turn_id, action_id=action_id, idempotency_key=idempotency_key)
        self.events.append(event)
        return event

    def _checkpoint(self, *args, **kwargs):
        return runtime_persistence._checkpoint(self, *args, **kwargs)

    def _load_existing_checkpoint(self, *args, **kwargs):
        return runtime_persistence._load_existing_checkpoint(self, *args, **kwargs)

    def _load_existing_events(self, *args, **kwargs):
        return runtime_persistence._load_existing_events(self, *args, **kwargs)

    def _restore_checkpoint(self, *args, **kwargs):
        return runtime_persistence._restore_checkpoint(self, *args, **kwargs)
















__all__ = ["AgentRuntime", "AgentState", "RuntimeConfig", "RuntimeResult"]
