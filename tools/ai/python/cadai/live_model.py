"""Controller-owned live modeling session and compatibility protocol exports.

LiveModelSession is the sole state/lock/worker owner. Protocol parsing and result
projection belong to stateless modules. Retain the imported public protocol names
until controller, transport, MCP and supported external callers migrate to those
owners; remove only those aliases at the next incompatible API version.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from .live_messages import parse_live_message
from .live_messages import parse_live_status as parse_live_status
from .live_protocol import (
    _IDENTIFIER,
    DEFAULT_DEBOUNCE_MS,
    LIVE_PROTOCOL_VERSION,
    MAX_EVENT_HISTORY,
    MAX_STATUS_BYTES,
    LiveModelProtocolError,
    _copy_checked,
    _identifier,
)
from .live_protocol import (
    MAX_DEBOUNCE_MS as MAX_DEBOUNCE_MS,
)
from .live_protocol import (
    MAX_LIVE_ID_LENGTH as MAX_LIVE_ID_LENGTH,
)
from .live_protocol import (
    loads_strict as loads_strict,
)
from .live_result import _bounded_result, _sanitize_error_text

Runner = Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]


EventSink = Callable[[Mapping[str, Any]], None]


class LiveModelSession:
    """One controller-owned, single-flight live modeling session."""

    def __init__(
        self,
        *,
        runner: Runner,
        event_sink: EventSink | None = None,
        session_id: str | None = None,
        clock: Callable[[], float] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._runner = runner
        self._event_sink = event_sink
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._cancel_event = cancel_event or self._stop
        self._worker: threading.Thread | None = None
        self._config: dict[str, Any] | None = None
        self._pending: dict[str, Any] | None = None
        self._latest: dict[str, Any] | None = None
        self._active: dict[str, Any] | None = None
        self._last_sequence = 0
        self._state = "IDLE"
        self._result: dict[str, Any] = {}
        self._publication = "DISABLED"
        self._output_sequence = 0
        self._history: deque[dict[str, Any]] = deque(maxlen=MAX_EVENT_HISTORY)
        candidate = session_id or uuid.uuid4().hex
        if _IDENTIFIER.fullmatch(candidate) is None:
            raise ValueError("invalid live-model session id")
        self.session_id = candidate

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel_event

    def handle(self, value: Mapping[str, Any]) -> dict[str, Any]:
        message = parse_live_message(value)
        event = message["event"]
        if event == "live_model.start":
            return self.start(message)
        if event == "live_model.source_changed":
            return self.source_changed(message)
        if event == "live_model.status":
            with self._lock:
                self._emit_locked(code="status_requested")
                return self._status_locked()
        if event == "live_model.stop":
            return self.stop()
        if event == "live_model.approve_publish":
            return self.approve_publish(message["approval_id"])
        return self.cancel_publish()

    def start(self, message: Mapping[str, Any]) -> dict[str, Any]:
        parsed = parse_live_message(message)
        if parsed["event"] != "live_model.start":
            raise LiveModelProtocolError("invalid_event", "expected live_model.start")
        with self._lock:
            # A live session owns one immutable recipe/profile binding at a
            # time.  Reconfiguring it while a worker is active could apply a
            # late result from the old source to the new target.  Require an
            # explicit stop before starting a different session.
            if self._config is not None and self._state != "STOPPED":
                self._result = {
                    "code": "session_already_running",
                    "message": "stop the active live-model session before starting another",
                }
                self._emit_locked(code="session_already_running")
                return self._status_locked()
            if self._state == "STOPPED":
                worker = self._worker
                if worker is not None and worker.is_alive():
                    self._result = {
                        "code": "session_restart_pending",
                        "message": "the previous live-model worker is still stopping",
                    }
                    self._emit_locked(code="session_restart_pending")
                    return self._status_locked()
                self._stop.clear()
                self._cancel_event.clear()
                self._worker = None
                self._state = "IDLE"
            self._config = _copy_checked(parsed)
            self._pending = None
            self._latest = None
            self._active = None
            self._last_sequence = 0
            self._result = {}
            self._publication = "DISABLED"
            self._emit_locked(code="session_started")
            return self._status_locked()

    def source_changed(self, message: Mapping[str, Any]) -> dict[str, Any]:
        parsed = parse_live_message(message)
        if parsed["event"] != "live_model.source_changed":
            raise LiveModelProtocolError("invalid_event", "expected live_model.source_changed")
        with self._lock:
            if self._config is None or self._state == "STOPPED":
                self._state = "ERROR"
                self._result = {"code": "session_not_started"}
                self._emit_locked(code="session_not_started")
                return self._status_locked()
            sequence = int(parsed["sequence"])
            if sequence <= self._last_sequence:
                self._emit_locked(code="duplicate_sequence", rejected_sequence=sequence)
                return self._status_locked()
            configured_target = self._config.get("target")
            if configured_target is not None and parsed["target"] != configured_target:
                self._emit_locked(code="target_mismatch", rejected_sequence=sequence)
                return self._status_locked()
            configured_view = self._config.get("view_identity")
            if configured_view is not None and parsed["view_identity"] != configured_view:
                self._emit_locked(code="view_mismatch", rejected_sequence=sequence)
                return self._status_locked()
            self._last_sequence = sequence
            self._latest = _copy_checked(parsed)
            self._pending = _copy_checked(parsed)
            self._state = "DEBOUNCING"
            self._result = {"code": "source_queued"}
            self._emit_locked(code="source_queued")
            self._ensure_worker_locked()
            self._condition.notify_all()
            return self._status_locked()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_locked()

    def events(self, limit: int = 32) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 128:
            raise LiveModelProtocolError("invalid_number", "event limit is outside the bounded range")
        with self._lock:
            # Never hand out references to nested status/result metadata.
            return [_copy_checked(item) for item in list(self._history)[-limit:]]

    def stop(self, *, emit: bool = True) -> dict[str, Any]:
        """Stop the session and boundedly join its worker.

        Public stop requests emit a final status event.  Controller teardown
        calls :meth:`close`, which uses ``emit=False`` so a retired transport
        cannot receive a misleading late ``session_stopped`` notification.
        """
        with self._lock:
            self._stop.set()
            self._cancel_event.set()
            self._pending = None
            self._latest = None
            self._state = "STOPPED"
            self._result = {"code": "session_stopped"}
            worker = self._worker
            self._condition.notify_all()
            if emit:
                self._emit_locked(code="session_stopped")
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=2.0)
        with self._lock:
            if self._worker is worker and (worker is None or not worker.is_alive()):
                self._worker = None
        return self.status()

    def close(self) -> None:
        self.stop(emit=False)

    def approve_publish(self, approval_id: str) -> dict[str, Any]:
        _identifier(approval_id, "approval_id")
        with self._lock:
            # The live M2 path has no publisher.  Recording an approval must
            # never turn into an implicit OA/text-view write.
            self._publication = "REFUSED_NO_PUBLISHER"
            self._result = {"code": "publication_requires_separate_publisher"}
            self._emit_locked(code="publication_refused")
            return self._status_locked()

    def cancel_publish(self) -> dict[str, Any]:
        with self._lock:
            self._publication = "CANCELLED"
            self._result = {"code": "publication_cancelled"}
            self._emit_locked(code="publication_cancelled")
            return self._status_locked()

    def _ensure_worker_locked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="aivw-live-model",
            daemon=True,
        )
        self._worker.start()

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                pending = self._pending
                config = _copy_checked(self._config or {})
                if pending is None or not config:
                    # Publish the idle marker while holding the same lock as
                    # ``source_changed``.  A callback arriving immediately
                    # after this point will observe ``None`` and create a new
                    # worker instead of being stranded behind a live thread
                    # that is about to return.
                    if self._worker is threading.current_thread():
                        self._worker = None
                    return
                self._pending = None
                debounce = float(config.get("debounce_ms", DEFAULT_DEBOUNCE_MS)) / 1000.0
                deadline = self._clock() + debounce
                # A save arriving during the debounce interval replaces the
                # captured event and restarts the interval.
                while not self._stop.is_set():
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        break
                    self._condition.wait(timeout=remaining)
                    if self._pending is not None:
                        pending = self._pending
                        self._pending = None
                        deadline = self._clock() + debounce
                if self._stop.is_set():
                    return
                active = _copy_checked(pending)
                self._active = active
                self._state = "SNAPSHOTTING"
                self._result = {"code": "run_started"}
                self._emit_locked(code="snapshot_started")
            try:
                with self._lock:
                    if self._stop.is_set():
                        return
                    self._state = "GENERATING"
                    self._emit_locked(code="generation_started")
                with self._lock:
                    if self._stop.is_set():
                        return
                    self._state = "VALIDATING"
                    self._emit_locked(code="validation_started")
                # The runner receives a private mutable snapshot.  It may
                # normalize or annotate its arguments, but cannot mutate the
                # session's configuration, latest event, or active record.
                raw_result = self._runner(_copy_checked(config), _copy_checked(active))
                if not isinstance(raw_result, Mapping):
                    raise LiveModelProtocolError("invalid_result", "live-model runner returned a non-object")
                result = _bounded_result(raw_result)
            except LiveModelProtocolError as exc:
                result = {"code": _sanitize_error_text(exc.code, 128), "message": _sanitize_error_text(exc)}
            except TimeoutError as exc:
                result = {"code": "runner_timeout", "message": _sanitize_error_text(exc)}
            except Exception as exc:  # provider/EDA boundary: fail closed
                provider_code = getattr(exc, "code", None)
                if isinstance(provider_code, str) and provider_code:
                    result = {
                        "code": _sanitize_error_text(provider_code, 128),
                        "message": _sanitize_error_text(exc),
                    }
                else:
                    result = {"code": "runner_error", "message": _sanitize_error_text(exc)}
            with self._lock:
                if self._stop.is_set():
                    # A stop is terminal for this generation.  Do not let a
                    # late provider result resurrect the session or publish
                    # a status after the owner has retired it.
                    self._active = None
                    return
                self._active = None
                latest = self._latest
                stale = bool(
                    latest
                    and (
                        latest.get("sequence") != active.get("sequence")
                        or latest.get("source_generation") != active.get("source_generation")
                    )
                )
                # A runner may report an authoritative snapshot hash.  That
                # hash is intentionally a different namespace from the
                # optimistic save token carried by the event.  Only an
                # explicit event-bound generation (or the legacy
                # ``source_generation`` field when no snapshot field exists)
                # participates in stale detection.
                requested_generation = result.get("requested_source_generation")
                if requested_generation is None and not any(
                    key in result
                    for key in ("snapshot_source_generation", "validated_source_generation")
                ):
                    requested_generation = result.get("source_generation")
                if (
                    requested_generation is not None
                    and requested_generation != active.get("source_generation")
                ):
                    stale = True
                if stale:
                    self._state = "STALE"
                    self._result = {
                        "code": "stale_source",
                        "discarded_sequence": active.get("sequence"),
                        "discarded_source_generation": active.get("source_generation"),
                    }
                    self._emit_locked(code="stale_source")
                    latest_changed = bool(
                        latest
                        and (
                            latest.get("sequence") != active.get("sequence")
                            or latest.get("source_generation")
                            != active.get("source_generation")
                        )
                    )
                    if self._pending is None and latest_changed:
                        self._pending = _copy_checked(latest)
                    continue
                self._result = result
                result_status = result.get("result_status", "")
                if result_status == "PASS":
                    self._state = "AWAITING_APPROVAL"
                    self._publication = "HUMAN_APPROVAL_REQUIRED"
                    self._emit_locked(code="candidate_ready")
                elif str(result.get("code", "")).startswith("stale"):
                    self._state = "STALE"
                    self._emit_locked(code="stale_source")
                else:
                    self._state = "ERROR"
                    self._emit_locked(code=str(result.get("code", "validation_failed"))[:128])

    def _status_locked(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "protocol_version": LIVE_PROTOCOL_VERSION,
            "event": "live_model.status",
            "session_id": self.session_id,
            "state": self._state,
            "publication": self._publication,
            "last_sequence": self._last_sequence,
        }
        if self._config is not None:
            for key in ("recipe_id", "through", "profile"):
                payload[key] = self._config[key]
            if "target" in self._config:
                payload["target"] = dict(self._config["target"])
            if "view_identity" in self._config:
                payload["view_identity"] = dict(self._config["view_identity"])
        if self._latest is not None:
            payload["source_generation"] = self._latest["source_generation"]
            payload["view_identity"] = dict(self._latest["view_identity"])
            payload["target"] = dict(self._latest["target"])
        if self._active is not None:
            payload["active_sequence"] = self._active["sequence"]
            payload["active_source_generation"] = self._active["source_generation"]
        if self._result:
            payload["result"] = dict(self._result)
        # Do not expose references to the session's mutable nested state.
        return _copy_checked(payload)

    def _emit_locked(self, *, code: str, **extra: Any) -> None:
        self._output_sequence += 1
        payload = self._status_locked()
        payload["event_sequence"] = self._output_sequence
        payload["code"] = str(code)[:128]
        for key, value in extra.items():
            if key.casefold() in {"token", "credential", "secret", "password", "authorization"}:
                continue
            payload[key] = value
        try:
            checked = _copy_checked(payload)
            encoded = json.dumps(checked, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
            if len(encoded) > MAX_STATUS_BYTES:
                checked = {
                    "protocol_version": LIVE_PROTOCOL_VERSION,
                    "event": "live_model.status",
                    "session_id": self.session_id,
                    "state": self._state,
                    "publication": self._publication,
                    "last_sequence": self._last_sequence,
                    "code": "status_bounded",
                    "event_sequence": self._output_sequence,
                }
        except (LiveModelProtocolError, TypeError, ValueError):
            checked = {
                "protocol_version": LIVE_PROTOCOL_VERSION,
                "event": "live_model.status",
                "session_id": self.session_id,
                "state": "ERROR",
                "publication": self._publication,
                "last_sequence": self._last_sequence,
                "code": "status_sanitization_failed",
                "event_sequence": self._output_sequence,
            }
        self._history.append(_copy_checked(checked))
        sink = self._event_sink
        if sink is not None:
            try:
                sink(_copy_checked(checked))
            except Exception:
                # A disconnected Virtuoso must not kill the worker or trigger
                # an implicit retry.  The status remains in local history.
                pass


__all__ = [
    "DEFAULT_DEBOUNCE_MS",
    "LIVE_PROTOCOL_VERSION",
    "LiveModelProtocolError",
    "LiveModelSession",
    "MAX_STATUS_BYTES",
    "loads_strict",
    "parse_live_message",
    "parse_live_status",
]
