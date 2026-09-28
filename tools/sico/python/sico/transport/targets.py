"""Immutable targets supplied only by the owning SKILL runtime's private pipe."""

from __future__ import annotations

import threading

from ..core.contracts import BoundContext


class TargetRegistry:
    def __init__(self, initial):
        self.initial = initial
        self.targets = {initial.target_id: initial}
        self.lock = threading.RLock()

    def validate(self, context):
        """Caller may validate admission while holding lock before publishing events."""
        if (context.instance_id, context.generation) != (
            self.initial.instance_id,
            self.initial.generation,
        ):
            raise ValueError("Quick input belongs to another Virtuoso instance")
        old = self.targets.get(context.target_id)
        if old and old.record() != context.record():
            raise ValueError("Target identity was reused with different data")
        if not old and len(self.targets) >= 64:
            raise ValueError("Too many pending targets")

    def add(self, context):
        with self.lock:
            self.validate(context)
            self.targets[context.target_id] = context

    def get(self, target_id):
        with self.lock:
            return self.targets.get(target_id)

    def records(self):
        with self.lock:
            return [context.record() for context in self.targets.values()]

    def release(self, target_id):
        with self.lock:
            if target_id != self.initial.target_id:
                self.targets.pop(target_id, None)


def submission(message):
    from ..core.contracts import identifier

    if (
        not isinstance(message, dict)
        or set(message) != {"kind", "id", "text", "context"}
        or message["kind"] != "submit"
    ):
        raise ValueError("Invalid quick input envelope")
    identifier(message["id"])
    text = message["text"]
    if not isinstance(text, str) or not text.strip() or len(text) > 16000 or "\0" in text:
        raise ValueError("Quick input is empty or too long")
    try:
        context = BoundContext.from_record(message["context"])
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid quick input context") from exc
    if context.target_id != message["id"]:
        raise ValueError("Quick input ID must identify its immutable target")
    return context
