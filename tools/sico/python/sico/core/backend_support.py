"""Supported task-engine capabilities; availability still requires a live admitted task."""

from dataclasses import dataclass


class UnsupportedCapability(ValueError):
    code = "backend_unsupported"

    def __init__(self, backend, capability):
        self.backend, self.capability = backend, capability
        super().__init__(f"当前 {backend} 后端不支持 {capability}；请使用支持该能力的新会话")


@dataclass(frozen=True)
class BackendSupport:
    kind: str
    role: str
    capabilities: frozenset[str]

    def supports(self, capability):
        return capability in self.capabilities

    def require(self, capability):
        if not self.supports(capability):
            raise UnsupportedCapability(self.kind, capability)


_SHARED = frozenset({
    "text_turns", "text_attachments", "tool_receipts", "cancel", "reconcile_no_replay",
    "target_validation", "host_target_decisions", "host_pdk_update", "workbench_reports",
})
PYTHON_SUPPORT = BackendSupport("python", "provider_compatibility", _SHARED)
CODEX_SUPPORT = BackendSupport("codex", "primary", _SHARED | {
    "native_input", "native_approval", "elicitation", "child_tasks", "pdk_selection",
    "turn_options", "resources", "thread_operations", "steering", "recap",
    "feature_settings",
})
