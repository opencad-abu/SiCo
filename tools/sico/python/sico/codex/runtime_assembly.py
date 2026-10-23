"""Bridge, scope, Codex process and thread startup for one session runtime."""

from __future__ import annotations

from .backend_instructions import INSTRUCTIONS
from .mcp import ToolBridge
from .native_history import HistoryUnavailable
from .runtime import CodexRuntime
from .settings import DEFAULT_TOOL_TIMEOUT
from .thread_startup import prepare_thread


def _discard_runtime(backend):
    runtime, backend.runtime = backend.runtime, None
    bridge, backend.bridge = backend.bridge, None
    try:
        if runtime is not None:
            runtime.close()
    finally:
        if bridge is not None:
            bridge.close()


def assemble(backend):
    """Build the session runtime once; the caller owns close-on-failure.

    The profile scope is resolved here, after the host tools are fully
    assembled, and feeds both the bridge and the Codex enabled_tools filter.
    """
    from ..adapters.tool_profiles import host_scope
    from .profile_setting import record_scope

    scope = host_scope(backend.tool_profile.profile, backend.tools.mcp_schemas())
    record_scope(backend, scope)
    from ..adapters.session_workflow import enter as enter_workflow
    from ..adapters.session_workflow import workflow_id

    def adopt_workflow(workflow, operation):
        return enter_workflow(
            backend.journal, workflow, backend.tools.mcp_schemas(),
            backend.state.context, source="automatic_operation")

    backend.bridge = ToolBridge(
        backend.tools, backend._execute,
        timeout=getattr(backend.provider, "tool_timeout", DEFAULT_TOOL_TIMEOUT),
        scope=scope,
        workflow=lambda: workflow_id(backend.journal),
        adopt_workflow=adopt_workflow,
    )
    try:
        from .audit_instructions import INSTRUCTIONS as AUDIT_INSTRUCTIONS
        from ..core.workbench_instructions import INSTRUCTIONS as WORKBENCH_INSTRUCTIONS

        backend.resources.prepare()
        backend.runtime = CodexRuntime(
            backend.provider, backend.journal, backend.bridge,
            memory_settings=backend.thread_ops.memory.pending,
            enabled_tools=tuple(scope["tools"]),
            external_resources=backend.resources.config.document["manifest"],
            terminal_route=backend.terminal is not None,
            cancelled=lambda: backend.active and (backend.cancelled() or backend.stale))
        backend.input_connection()
        backend.runtime.rpc.notification_handler = backend._notification
        backend.resources.register(backend.runtime.rpc)
        prepare_thread(
            backend.runtime, backend.thread_id, provider=backend.provider,
            options=backend.turn_options, operations=backend.thread_ops,
            resources=backend.resources, names=backend.names,
            history=backend.native_history, bind_thread=backend._bind_thread,
            has_audit=hasattr(backend, "audit"), has_workbench=hasattr(backend, "workbench"),
            instructions=INSTRUCTIONS, workbench_instructions=WORKBENCH_INSTRUCTIONS,
            audit_instructions=AUDIT_INSTRUCTIONS)
    except HistoryUnavailable:
        _discard_runtime(backend)
        raise
    except BaseException:
        backend.close()
        raise


__all__ = ["assemble"]
