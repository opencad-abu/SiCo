"""Worker-side capture of session state and committed event ranges."""

from copy import deepcopy

from ..core.contracts import json_copy
from .events import SESSION_CONTRACT


class SessionUpdates:
    def __init__(self, journal, *, lock, execution, bindings, history,
                 steering_scope, steering_supported, router_snapshot):
        self.journal = journal
        self._lock = lock
        self.execution = execution
        self.bindings = bindings
        self.history = history
        self.steering_scope = steering_scope
        self.steering_supported = steering_supported
        self.router_snapshot = router_snapshot

    def read_updates(self, after=0, limit=100, *, since_version=None, blocking=True,
                     max_bytes=None, publisher=None):
        view = self.execution()
        context = view.current
        batch = {
            "contract": SESSION_CONTRACT, "session_id": view.session_id,
            "runtime_id": view.runtime_id, "instance_id": context.instance_id,
            "generation": context.generation, "events": [], "snapshot": None,
            "committed_sequence": after,
        }
        if not self._lock.acquire(blocking=blocking):
            return batch
        try:
            if max_bytes is None:
                events = self.journal.events(after, limit=limit, blocking=blocking)
                if events is None:
                    return batch
                committed = after + len(events)
                caught_up = len(events) < limit
            else:
                events, committed = self.journal.committed_batch(after, limit, max_bytes)
                caught_up = after + len(events) == committed
            snapshot = None
            if caught_up and (since_version != self.execution().version
                              or (publisher is not None and publisher.changed())):
                snapshot = (publisher.read(committed) if publisher is not None
                            else self._state_snapshot(committed))
            batch.update(events=events, snapshot=snapshot, committed_sequence=committed)
            return batch
        finally:
            self._lock.release()

    def _state_snapshot(self, committed):
        view = self.execution()
        displayed = self.bindings.publish_displayed()
        return {
            "version": view.version, "sequence": committed,
            "busy": view.busy, "paused": view.paused, "closing": view.closing,
            "fault": view.fault, "task": json_copy(view.task),
            "steering": self.steering_scope(),
            "steering_supported": self.steering_supported,
            "cancelling": view.busy and view.cancelled,
            "context": view.current.record(),
            "bindings": [context.record() for context in self.bindings.events.targets.values()],
            "binding_state": self.bindings.events.snapshot(),
            "binding_error": self.bindings.sync_error,
            "default_context": displayed.record(), "pending": view.pending,
            "requests": self.history.snapshot(),
            "router": deepcopy(self.router_snapshot(view.current)),
        }
