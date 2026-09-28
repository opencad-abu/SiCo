"""Versioned immutable session snapshots generated only on the event worker."""

from ..core.contracts import BoundContext
from .event_display import bounded, encoded
from .published import freeze

STATE_BYTES = 524288


def state_view(snapshot):
    shown = dict(snapshot)
    binding = snapshot.get("binding_state") or {}
    if (len(snapshot["requests"]) > 200
            or any(len(binding.get(key, [])) > 64 for key in
                   ("targets", "reservations", "operations", "proposals", "holds", "recent"))
            or len((snapshot.get("router") or {}).get("requests", [])) > 64):
        raise ValueError("Session control inventory exceeds the display limit")
    task = snapshot["task"]
    keys = ("id", "status", "diagnostic", "activity", "waiting_audits", "input_tokens",
            "output_tokens", "total_tokens", "context_usage", "steer_turn", "steer_blocked")
    shown["task"], _ = bounded({key: task[key] for key in keys if key in task}, chars=1024)
    for key in ("id", "waiting_audits"):
        if key in task:
            shown["task"][key] = task[key]
    shown["requests"] = []
    for row in snapshot["requests"]:
        item = dict(row)
        item["text"] = str(item.get("text", ""))[:512]
        item["diagnostic"] = str(item.get("diagnostic", ""))[:512]
        shown["requests"].append(item)
    contexts = (BoundContext.from_record(snapshot["context"]),
                BoundContext.from_record(snapshot["default_context"]))
    for context in contexts:
        object.__setattr__(context, "snapshot", freeze(context.snapshot))
    # Control identities are exact. Never truncate a target or silently omit
    # controls when an abnormal snapshot exceeds the supported display size.
    if len(encoded(shown)) > STATE_BYTES:
        raise ValueError("Session control snapshot exceeds the display limit")
    return freeze(shown), contexts


class StatePublication:
    def __init__(self, controller):
        self.controller = controller
        self._key = None
        self._published = None

    def key(self):
        owner = self.controller
        context = owner.current
        return (owner.version, context.instance_id, context.generation,
                owner.closing, owner._cancel.is_set(), owner.paused)

    def changed(self):
        return self.key() != self._key

    def read(self, committed):
        owner, key = self.controller, self.key()
        if key != self._key:
            from .frontend_session import publish_session

            publish_session(owner)
            self._published = state_view(owner._state_snapshot(committed))
            self._key = key
        state, contexts = self._published
        # The versioned state may stay unchanged while journal-only evidence
        # advances. Updating this envelope shares the immutable nested payload.
        return freeze({**state, "sequence": committed}), contexts
