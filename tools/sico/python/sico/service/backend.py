"""Select a task engine explicitly, without routing Codex through the Python model loop."""

from ..codex.settings import CodexSettings
from ..core.loop import AgentLoop


def create_backend(provider, tools, journal, context, *, workbench=True):
    """Build the backend for one session journal.

    ``workbench=False`` yields a metadata-only backend for session rename and
    delete. It must not register workbench tools on a shared registry or
    replace its validators, because the owning window may still be running
    tasks against them.
    """
    if isinstance(provider, CodexSettings):
        from ..codex.backend import CodexBackend

        backend = CodexBackend(provider, tools, journal, context)
    else:
        if any(event["kind"] == "codex.thread" for event in journal.events()):
            raise ValueError("A Codex session cannot be resumed with the Python backend")
        backend = AgentLoop(provider, tools, journal, context)
    if not workbench:
        return backend
    from .workbench import attach_workbench

    try:
        backend.workbench = attach_workbench(backend)
        tools.target_choice_validator = backend.audit.target_choice
        tools.pdk_update_validator = backend.audit.pdk_update_confirmation
        tools.settled_target_action = backend.audit.settled_target_action
        if isinstance(provider, CodexSettings):
            tools.pdk_choice_validator = backend.audit.pdk_choice
    except BaseException:
        close = getattr(backend, "close", None)
        if callable(close):
            close()
        raise
    return backend
