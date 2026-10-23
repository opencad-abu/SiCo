"""Own safe retirement and pre-dispatch recovery of a session Codex runtime."""

from __future__ import annotations

from contextlib import contextmanager

from ..core.contracts import TERMINAL
from .mcp import ToolBridge
from .native_history import HistoryUnavailable
from .runtime import CodexRuntime
from .rpc import RpcError
from .thread_startup import metadata_connection
from .turn_input import idle_request

_CLEANUP_UNCONFIRMED = "Codex runtime cleanup unconfirmed; ownership is retained"


def runtime_stopped(runtime):
    rpc = getattr(runtime, "rpc", None)
    stopped = getattr(rpc, "stopped", None)
    return rpc is None or callable(getattr(stopped, "is_set", None)) and stopped.is_set()


def recover_interrupted_task(backend, records):
    """Settle a task left non-terminal by an earlier process."""
    task = backend.state.task
    if not task or task.get("status") in TERMINAL:
        return
    task_id = task.get("id", "")
    dispatched = any(
        event["kind"] == "tool.started" and event.get("task_id") == task_id
        for event in records)
    backend._cancel_baseline = True
    backend._finish("needs_reconcile" if dispatched else "cancelled",
                    "Previous Codex task was interrupted; no replay")


def _quiescent(backend):
    task = backend.state.task
    receipt = getattr(backend.thread_ops, "receipt", None) or {}
    return not backend.active and not backend._inflight and not (
        task and (task.get("status") not in TERMINAL or task.get("pending_calls")
                  or task.get("waiting_audits"))
    ) and receipt.get("status") != "executing"


def authorize_new_task_recovery(backend):
    """Capture recovery permission before the new task replaces terminal state."""
    if not _quiescent(backend):
        return False
    if backend.runtime is not None:
        # The process may stop between task admission and its first RPC.
        # Permission is consumed once at that pre-dispatch boundary.
        return True
    if backend._retired_runtime_recovery:
        backend._retired_runtime_recovery = False
        return True
    return False


def ensure_cleanup_confirmed(backend):
    if backend._runtime_cleanup_unconfirmed:
        raise RpcError(_CLEANUP_UNCONFIRMED)


def retire_runtime(backend):
    runtime = backend.runtime
    if runtime is not None:
        try:
            runtime.close()
            backend.runtime = None
        except Exception as exc:
            backend._runtime_cleanup_unconfirmed = True
            raise RpcError(_CLEANUP_UNCONFIRMED, code="cleanup_unconfirmed") from exc
    bridge = backend.bridge
    if bridge is not None:
        try:
            bridge.close()
            backend.bridge = None
        except Exception as exc:
            backend._runtime_cleanup_unconfirmed = True
            raise RpcError(_CLEANUP_UNCONFIRMED, code="cleanup_unconfirmed") from exc
    backend._runtime_cleanup_unconfirmed = False


def retire_if_stopped(backend):
    if (backend.runtime is not None and runtime_stopped(backend.runtime)
            and _quiescent(backend)):
        retire_runtime(backend)
        backend._retired_runtime_recovery = True
        return True
    return False


def ensure_runtime(backend, *, recover_stopped=False):
    ensure_cleanup_confirmed(backend)
    runtime = backend.runtime
    if runtime is not None and not runtime_stopped(runtime):
        return runtime.rpc
    if runtime is not None:
        if not recover_stopped:
            retire_if_stopped(backend)
            raise RpcError("Codex runtime stopped; no task was replayed")
        retire_runtime(backend)
    elif backend.bridge is not None:
        retire_runtime(backend)
    from .runtime_assembly import assemble

    try:
        assemble(backend)
    except HistoryUnavailable:
        # History repair is optional. A paging timeout may stop its connection;
        # rebuild once without it so the admitted task is not replaced by a
        # reconciliation request.
        assemble(backend)
    backend._retired_runtime_recovery = False
    backend._runtime_cleanup_unconfirmed = False
    return backend.runtime.rpc


@contextmanager
def metadata_rpc(backend):
    """Open a metadata connection under the session runtime owner."""
    ensure_cleanup_confirmed(backend)
    if backend.runtime or not backend.thread_id:
        with runtime_rpc(backend) as rpc:
            yield rpc
        return
    with metadata_connection(
            backend.provider, backend.journal, backend.tools, backend._execute,
            backend._notification, bridge_factory=ToolBridge,
            runtime_factory=CodexRuntime) as rpc:
        yield rpc


@contextmanager
def runtime_rpc(backend):
    """Hold the owned RPC for one metadata read and retire it if it stops."""
    ensure_runtime(backend)
    runtime = backend.runtime
    try:
        yield runtime.rpc
    finally:
        if runtime is backend.runtime and runtime_stopped(runtime):
            retire_if_stopped(backend)


def poll_runtime(backend):
    """Drain local idle notifications and retire a stopped quiescent runtime."""
    runtime = backend.runtime
    if runtime is None:
        return
    if runtime_stopped(runtime):
        retire_if_stopped(backend)
        return
    task = backend.state.task
    if (backend.active or backend._inflight or task and (
            task.get("status") not in TERMINAL or task.get("pending_calls")
            or task.get("waiting_audits"))):
        return
    runtime.rpc.poll_notifications()
    runtime.rpc.poll_requests(lambda event: idle_request(
        event, active=backend.active, audit=getattr(backend, "audit", None),
        rpc=runtime.rpc, emit=backend._event))


__all__ = [
    "authorize_new_task_recovery",
    "ensure_cleanup_confirmed",
    "ensure_runtime",
    "metadata_rpc",
    "poll_runtime",
    "retire_if_stopped",
    "retire_runtime",
    "runtime_rpc",
    "runtime_stopped",
]
