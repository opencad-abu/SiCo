"""Session projection of durable Bridge binding events, independent of task state."""

import time

from ..core.contracts import BoundContext, json_copy


class SessionBindingEvents:
    def __init__(self, journal, initial_context, *, read_source, changed):
        self.journal = journal
        self.read_source = read_source
        self.changed = changed
        self.targets = {initial_context.target_id: initial_context}
        self.default_context = initial_context
        self.cursor = 0
        self.state = None
        self.sampled_at = 0
        self.default_explicit = False
        self.mirrored = {
            (row["kind"], row["payload"].get("event_id"))
            for row in journal.events()
            if row["kind"].startswith("binding.")
        }

    def refresh(self, *, expected_binding=None):
        broker, session_id, context = self.read_source()
        read = getattr(broker, "binding_snapshot", None)
        if not callable(read):
            return
        snapshot = read(session_id, self.cursor)
        if (expected_binding is not None
                and tuple(snapshot.get(key) for key in ("binding_id", "instance_id", "generation"))
                != expected_binding):
            raise ValueError("Binding snapshot belongs to a previous session binding")
        for event in snapshot["events"]:
            payload = event["payload"]
            key = (event["kind"], payload["event_id"])
            if key not in self.mirrored:
                self.journal.append_binding(event["kind"], payload, context)
                self.mirrored.add(key)
        self.default_explicit = snapshot["default_explicit"]
        self.cursor = snapshot.pop("event_cursor")
        snapshot.pop("events")
        # The Qt countdown advances locally; polling must not reset it every tick.
        comparison = json_copy(snapshot)
        for proposal in comparison["proposals"]:
            proposal.pop("remaining_seconds", None)
        previous = json_copy(self.state) if self.state else None
        if previous:
            for proposal in previous["proposals"]:
                proposal.pop("remaining_seconds", None)
        self.targets = {
            record["target_id"]: BoundContext.from_record(record) for record in snapshot["targets"]
        }
        self.default_context = self.targets[snapshot["default_target_id"]]
        if previous != comparison:
            self.state = snapshot
            self.sampled_at = time.monotonic()
            self.changed()

    def register(self, context):
        self.targets[context.target_id] = context

    def snapshot(self):
        state = json_copy(self.state) if self.state else None
        if state:
            elapsed = time.monotonic() - self.sampled_at
            for proposal in state["proposals"]:
                proposal["remaining_seconds"] = max(0, proposal["remaining_seconds"] - elapsed)
        return state

    def pending_targets(self):
        return {proposal["target_id"] for proposal in (self.state or {}).get("proposals", [])}
