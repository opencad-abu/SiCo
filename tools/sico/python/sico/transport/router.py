"""Bounded FIFO admission for one single-threaded Virtuoso generation."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque

from .methods import QueryUnavailable
from .relay import emit_diagnostic


class InstanceRegistry:
    """Own the router lifecycle for each live Virtuoso instance generation."""

    def __init__(self, diagnostic=None, *, queue_limit=64):
        self.diagnostic = diagnostic
        self.queue_limit = queue_limit
        self._lock = threading.RLock()
        self._routers = {}
        self._current = {}
        self._retired = set()

    def register(self, instance_id, generation):
        """Install one generation and close any older generation for the instance."""
        router, _ = self.register_with_status(instance_id, generation)
        return router

    def register_with_status(self, instance_id, generation):
        """Return ``(router, created)`` while changing generations atomically."""
        key = (instance_id, generation)
        with self._lock:
            if key in self._retired:
                raise ValueError("Virtuoso generation has been retired")
            existing = self._routers.get(key)
            if existing is not None:
                return existing, False
            old_generation = self._current.get(instance_id)
            if old_generation is not None and old_generation != generation:
                self._retired.add((instance_id, old_generation))
                old = self._routers.pop((instance_id, old_generation), None)
                if old is not None:
                    old.close()
            router = SkillInstanceRouter(
                instance_id, generation, self.diagnostic, queue_limit=self.queue_limit
            )
            self._routers[key] = router
            self._current[instance_id] = generation
            return router, True

    def get(self, instance_id, generation):
        with self._lock:
            if self._current.get(instance_id) != generation:
                return None
            return self._routers.get((instance_id, generation))

    def cancel(self, instance_id, generation, request_id, *, session_id=None, target_id=None):
        router = self.get(instance_id, generation)
        if router is None:
            return "router_unavailable"
        return router.cancel_request(request_id, session_id=session_id, target_id=target_id)

    def remove(self, instance_id, generation, *, expected=None):
        key = (instance_id, generation)
        with self._lock:
            router = self._routers.get(key)
            if router is None or (expected is not None and router is not expected):
                return None
            self._routers.pop(key, None)
            if self._current.get(instance_id) == generation:
                self._current.pop(instance_id, None)
        if router is not None:
            router.close()
        return router

    def snapshot(self):
        with self._lock:
            return [router.snapshot() for router in self._routers.values()]

    def close(self):
        with self._lock:
            routers = tuple(self._routers.values())
            self._routers.clear()
            self._current.clear()
        for router in routers:
            router.close()


class SkillInstanceRouter:
    def __init__(self, instance_id, generation, diagnostic=None, *, queue_limit=64, journal=None):
        self.instance_id, self.generation = instance_id, generation
        self.router_id = uuid.uuid5(
            uuid.NAMESPACE_URL, "cad-ai-router:" + instance_id + ":" + generation
        ).hex
        self.diagnostic, self.queue_limit = diagnostic, queue_limit
        self._changed = threading.Condition(threading.RLock())
        self._queue = deque()
        self._requests = {}
        self._active = None
        self._unknown = None
        self._unknown_since = None
        self._resolved = set()
        self._closed = False
        self.journal = journal
        self._skill_pending = None

    def _persist(self, row):
        if self.journal:
            try:
                self.journal.record(row)
            except (OSError, ValueError):
                self._closed = True
                raise QueryUnavailable("router_persistence_failed",
                                       "Cannot persist router state; no further dispatch is allowed") from None

    def _emit(self, event, **fields):
        fields.setdefault("router_id", self.router_id)
        emit_diagnostic(self.diagnostic, event, instance_id=self.instance_id,
                        generation=self.generation, **fields)

    def _available(self):
        if self._closed:
            raise QueryUnavailable(
                "router_unavailable", "Virtuoso router closed; request not dispatched"
            )
        if self._unknown:
            raise QueryUnavailable("skill_blocked_unknown",
                "Previous SKILL outcome is unknown; request not dispatched (request_id="
                + self._unknown + ")")

    def execute(self, method, operation, *, wait=False, queue_timeout=30, execution_timeout=1800,
                execution_mode="foreground",
                cancelled=None, progress=None, **identity):
        if execution_mode not in {"foreground", "background"}:
            raise ValueError("Invalid execution mode")
        request_id = uuid.uuid4().hex
        row = dict(
            router_id=self.router_id,
            request_id=request_id,
            method=method,
            execution_mode=execution_mode,
            accepted_at=time.time(),
            **identity,
        )
        row["queue_deadline"] = row["accepted_at"] + queue_timeout
        deadline = time.monotonic() + queue_timeout

        def notify(state, *, persisted=False, **extra):
            record = dict(row, state=state, **extra)
            if not persisted:
                self._persist(record)
            if progress:
                try:
                    progress(record)
                except Exception:
                    # Status publication must not change the outcome of the
                    # serialized bridge operation. The diagnostic sink still
                    # records the request lifecycle below.
                    self._emit("router.status_failed", request_id=request_id,
                               state=state)

        with self._changed:
            try:
                self._available()
            except QueryUnavailable as exc:
                row["finished_at"] = time.time()
                state = {
                    "router_unavailable": "router_unavailable",
                    "skill_blocked_unknown": "blocked_unknown",
                }.get(exc.code, "rejected")
                notify(state, code=exc.code)
                raise
            if not wait and (self._active or self._queue):
                owner = self._active or (self._queue[0] if self._queue else None)
                owner_id = owner.get("request_id") if owner else None
                self._emit("router.queue", **row, queue_position=len(self._queue) + 1,
                           blocked_by=owner_id)
                notify("queued", queue_position=len(self._queue) + 1,
                       blocked_by=owner_id)
                raise QueryUnavailable(
                    "skill_queued",
                    "SKILL is busy; request not enqueued or dispatched"
                    + (f" (blocked_by={owner_id})" if owner_id else ""),
                )
            if len(self._queue) >= self.queue_limit:
                row["finished_at"] = time.time()
                notify("queue_full", code="skill_queue_full")
                raise QueryUnavailable(
                    "skill_queue_full", "SKILL queue is full; request not dispatched"
                )
            self._persist(dict(row, state="accepted"))
            self._queue.append(row)
            self._requests[request_id] = row
            self._emit("router.accept", **row)
            notify("accepted", persisted=True)
            try:
                if self._active or self._queue[0] is not row:
                    blocked_by = self._active["request_id"] if self._active else None
                    position = len(self._queue)
                    self._emit("router.queue", **row, queue_position=position,
                               blocked_by=blocked_by)
                    row["queued_at"] = time.time()
                    notify("queued", queue_position=position, blocked_by=blocked_by)
                while True:
                    if row.get("cancel_requested"):
                        row["finished_at"] = time.time()
                        raise QueryUnavailable(
                            "cancelled_before_start", "SKILL request cancelled before dispatch"
                        )
                    self._available()
                    if cancelled and cancelled():
                        row["finished_at"] = time.time()
                        raise QueryUnavailable(
                            "cancelled_before_start", "SKILL request cancelled before dispatch"
                        )
                    if self._active is None and self._queue[0] is row:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        row["finished_at"] = time.time()
                        raise QueryUnavailable(
                            "skill_queue_timeout", "Queue deadline expired; request not dispatched"
                        )
                    self._changed.wait(min(remaining, 0.1))
                row["started_at"] = time.time()
                row["execution_deadline"] = row["started_at"] + execution_timeout
                self._active = row
            except QueryUnavailable as exc:
                row["finished_at"] = row.get("finished_at", time.time())
                state = {
                    "router_unavailable": "router_unavailable",
                    "skill_blocked_unknown": "blocked_unknown",
                    "cancelled_before_start": "cancelled_before_start",
                    "skill_queue_timeout": "queue_timeout",
                }.get(exc.code, "rejected")
                notify(state, code=exc.code)
                raise
            finally:
                self._queue.remove(row)
                if self._active is not row:
                    self._requests.pop(request_id, None)
                self._changed.notify_all()
        try:
            notify("running")
            result = operation(request_id)
            if self._closed:
                raise QueryUnavailable("router_unavailable", "Router retired while awaiting the reply")
            row["finished_at"] = time.time()
            notify("completed")
            return result
        except Exception as exc:
            code = getattr(exc, "code", None)
            if self._unknown == request_id or code in {"skill_timeout", "skill_response_pending", "skill_reply_write_failed"} or (
                code is None
                and any(
                    value in str(exc)
                    for value in ("skill_timeout", "skill_response_pending", "skill_reply_write_failed")
                )
            ):
                self.mark_unknown(request_id)
                row["finished_at"] = time.time()
                notify("timed_out_unknown", code=code or "skill_timeout")
            elif self._closed:
                row["finished_at"] = time.time()
                notify("router_unavailable", code="router_unavailable")
            else:
                row["finished_at"] = time.time()
                notify("failed", code=code or type(exc).__name__)
            raise
        finally:
            with self._changed:
                self._active = None
                if self._unknown != request_id:
                    self._requests.pop(request_id, None)
                self._resolved.discard(request_id)
                self._changed.notify_all()

    def cancel_request(self, request_id, *, session_id=None, target_id=None):
        """Cancel queued work; running/unknown requests retain the execution slot.

        ``cancel_requested`` guarantees no dispatch. The execution thread
        publishes the terminal event; completed requests belong to the journal
        and return ``not_found`` here.
        """
        with self._changed:
            if self._closed:
                return "router_unavailable"
            row = self._requests.get(request_id)
            if row is None:
                return "not_found"
            if ((session_id is not None and row.get("session_id") != session_id)
                    or (target_id is not None and row.get("target_id") != target_id)):
                return "not_owner"
            if self._unknown == request_id:
                return "running_unknown"
            if self._active is row:
                return "running"
            if not row.get("cancel_requested"):
                self._persist(dict(row, state="cancel_requested", cancel_requested_at=time.time()))
                row["cancel_requested"] = True
                row["cancel_requested_at"] = time.time()
                self._emit("router.cancel_requested", **row)
            self._changed.notify_all()
            return "cancel_requested"

    def mark_unknown(self, request_id):
        with self._changed:
            if request_id not in self._resolved:
                self._unknown, self._unknown_since = request_id, time.time()
                if request_id in self._requests:
                    self._persist(dict(self._requests[request_id], state="timed_out_unknown",
                                       unknown_since=self._unknown_since))
                self._emit("router.timeout_unknown", request_id=request_id)
            self._changed.notify_all()

    def clear_unknown(self, request_id):
        with self._changed:
            if self._active and self._active["request_id"] == request_id:
                # A late response can race with the caller handling its timeout.
                self._resolved.add(request_id)
            if self._unknown == request_id:
                self._unknown, self._unknown_since = None, None
                if self._active is not self._requests.get(request_id):
                    self._requests.pop(request_id, None)
                self._emit("router.late_reply", request_id=request_id)
            self._changed.notify_all()

    def record_reply(self, request_id, reply, *, late=False):
        with self._changed:
            row = self._requests.get(request_id)
            saved = self.journal.lookup(request_id) if self.journal else None
            if row is None and saved is None:
                return False
            if not isinstance(reply, dict) or reply.get("id") != request_id or (
                "generation" in reply and reply["generation"] != self.generation
            ) or ("instance_id" in reply and reply["instance_id"] != self.instance_id):
                return False
            if saved and saved.get("reply") == reply:
                return True
            record = dict(row or saved, reply=reply, reply_received_at=time.time())
            if late or self._unknown == request_id or (saved and saved.get("state") == "late_reply"):
                record.update(state="late_reply", caller_state="timed_out_unknown")
            else:
                record["state"] = "running"
            self._persist(record)
            return True

    def skill_started(self, request_id):
        with self._changed:
            if self._skill_pending or self._closed or self._unknown:
                raise QueryUnavailable("skill_blocked_unknown", "SKILL execution slot is unavailable")
            if not self._active or self._active["request_id"] != request_id:
                raise ValueError("SKILL request was not admitted by this router")
            self._skill_pending = request_id

    def record_skill_stage(self, event):
        """Stage evidence never substitutes for the matching business reply."""
        from .skill_diagnostics import stage_record

        with self._changed:
            if self._closed or not self.journal:
                return False
            stage = stage_record(event, self.journal.identity)
            if stage is None:
                return False
            request_id = stage['request_id']
            saved = self.journal.lookup(request_id)
            if (not saved or not saved.get('started_at')
                    or stage['method'] != saved.get('method')
                    or stage['target_id'] != saved.get('target_id')):
                return False
            previous = saved.get('skill_stage')
            if previous and stage['sequence'] <= previous['sequence']:
                return stage == previous
            stages = saved.get('skill_stages', [])
            self._persist(dict(request_id=request_id, skill_stage=stage,
                               skill_stages=(stages + [stage])[-24:]))
            if (stage['event'] == 'skill.reply_write_failed'
                    and self._skill_pending == request_id and 'reply' not in saved):
                self.mark_unknown(request_id)
            self._changed.notify_all()
            return True

    def skill_pending(self):
        with self._changed:
            return self._skill_pending

    def skill_write_failed(self, request_id):
        with self._changed:
            row = self.journal.lookup(request_id) if self.journal else None
            return bool(row and 'reply' not in row and self._skill_pending == request_id
                        and row.get('skill_stage', {}).get('event') == 'skill.reply_write_failed')

    def skill_reply(self, reply):
        with self._changed:
            request_id = self._skill_pending
            if request_id is None or reply.get("id") != request_id:
                return False
            late = self._unknown == request_id or self._active is None
            if not self.record_reply(request_id, reply, late=late):
                return False
            self._skill_pending = None
            self.clear_unknown(request_id)
            return True

    def close(self):
        with self._changed:
            for row in self._requests.values():
                try:
                    self._persist(dict(row, state="timed_out_unknown" if row is self._active
                                       or row["request_id"] == self._unknown else "router_unavailable"))
                except QueryUnavailable:
                    break
            self._closed = True
            self._changed.notify_all()

    def snapshot(self):
        with self._changed:
            owner = self._unknown or (self._active["request_id"] if self._active else None)
            unknown = self._requests.get(self._unknown)
            return dict(instance_id=self.instance_id, generation=self.generation,
                router_id=self.router_id,
                active=self._active["request_id"] if self._active else None,
                active_request=dict(self._active, state="running") if self._active else None,
                unknown=self._unknown, unknown_since=self._unknown_since,
                unknown_request=(dict(unknown or {"request_id": self._unknown},
                                      state="timed_out_unknown") if self._unknown else None),
                queued=len(self._queue),
                queue=[dict(row, state="cancel_requested" if row.get("cancel_requested")
                            else "queued", queue_position=position, blocked_by=owner)
                       for position, row in enumerate(self._queue, 1)],
                closed=self._closed)

    def has_unknown(self):
        if not self._changed.acquire(blocking=False):
            return True
        try:
            return self._unknown is not None
        finally:
            self._changed.release()

    def session_unresolved(self, session_id):
        """Conservative ownership check callable while the broker lock is held."""
        # Router progress can call back into the broker. Never wait here and
        # invert that lock order while a desktop is releasing its bindings.
        if not self._changed.acquire(blocking=False):
            return True
        try:
            if self._unknown:
                row = self._requests.get(self._unknown)
                return row is None or row.get("session_id") == session_id
            return False
        finally:
            self._changed.release()
