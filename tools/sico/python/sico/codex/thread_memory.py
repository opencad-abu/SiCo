"""Explicit per-session memory configuration; no personal-home import."""

from ..core.contracts import json_copy


def validate_memory(value):
    if (not isinstance(value, dict) or set(value) != {"enabled", "generate", "use"}
            or any(type(flag) is not bool for flag in value.values())
            or (not value["enabled"] and (value["generate"] or value["use"]))):
        raise ValueError("Invalid explicit memory settings")
    return dict(value)


def configured_memory(journal):
    result = None
    for event in journal.events():
        if event["kind"] == "codex.memory.configured":
            result = validate_memory(event["payload"]["settings"])
    return result


def runtime_options(journal, settings=None, *, model=None):
    settings = validate_memory(settings) if settings is not None else (
        configured_memory(journal) or {"enabled": False, "generate": False, "use": False})
    options = {"features.memories": settings["enabled"],
               "memories.generate_memories": settings["generate"],
               "memories.use_memories": settings["use"]}
    if settings["enabled"] and model is not None:
        # Native memory jobs have their own model defaults. Keep them on the
        # project's provider model, including jobs started while restoring.
        options.update({"memories.extract_model": model, "memories.consolidation_model": model})
    return options


class ThreadMemory:
    def __init__(self, backend):
        self.backend = backend
        self.configured = configured_memory(backend.journal)
        self.pending = None
        self.effective = None
        self.connection_id = None

    def apply(self, rpc):
        settings = self.pending if self.pending is not None else self.configured
        if settings is not None:
            rpc.request("thread/memoryMode/set", {
                "threadId": self.backend.thread_id,
                "mode": "enabled" if settings["generate"] else "disabled",
            })

    def inspect(self):
        result = self.backend.runtime.rpc.request("config/read", {"includeLayers": False})
        config = result["config"]
        features, memories = config.get("features") or {}, config.get("memories") or {}
        effective = {
            "enabled": features.get("memories") is True,
            "generate": memories.get("generate_memories"),
            "use": memories.get("use_memories"),
        }
        settings = self.pending if self.pending is not None else self.configured
        if settings is not None and effective != settings:
            self.connection_id = None
            raise ValueError("Memory configuration did not match the runtime readback")
        if self.pending is not None:
            if self.backend.cancelled() or self.backend.stale:
                from .goals import GoalStopped

                raise GoalStopped("Memory configuration stopped before confirmation")
            self.backend._event("codex.memory.configured", {"settings": self.pending})
            self.configured, self.pending = self.pending, None
        if effective != self.effective or self.connection_id != self.backend.connection_id:
            self.backend._event("codex.memory.observed", {
                "thread_id": self.backend.thread_id, "settings": effective,
                "connection_id": self.backend.connection_id,
                "scope": "session", "home": str(self.backend.runtime.home),
            })
        self.effective = effective
        self.connection_id = self.backend.connection_id
        return self.snapshot()

    def configure(self, value):
        value = validate_memory(value)
        self.backend._event("codex.memory.requested", {"settings": value})
        self.pending, self.effective, self.connection_id = value, None, None

    def snapshot(self):
        return {"configured": json_copy(self.configured),
                "effective": json_copy(self.effective), "scope": "session",
                "verified": bool(self.backend.runtime and self.connection_id
                                 and self.connection_id == self.backend.connection_id)}
