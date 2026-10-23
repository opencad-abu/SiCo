"""Select a task engine explicitly, without routing Codex through the Python model loop."""

from ..codex.settings import CodexSettings
from .python_backend import PythonBackend
from ..storage.journal import BULK_KINDS


def create_backend(provider, tools, journal, context, *, workbench=True, terminal=None,
                   labels=None):
    """Build the backend for one session journal.

    ``workbench=False`` yields a metadata-only backend for session rename and
    delete. It must not register workbench tools on a shared registry or
    replace its validators, because the owning window may still be running
    tasks against them.
    """
    if isinstance(provider, CodexSettings):
        from ..codex.backend import CodexBackend

        backend = CodexBackend(provider, tools, journal, context, terminal=terminal)
    else:
        if any(row["kind"] == "codex.thread"
               for row in journal.events(exclude=BULK_KINDS)):
            raise ValueError("A Codex session cannot be resumed with the Python backend")
        backend = PythonBackend(provider, tools, journal, context)
    if not workbench:
        return backend
    from .workbench import attach_workbench

    try:
        backend.workbench = attach_workbench(backend)
        if labels is not None:
            from .navigation_label_tools import attach_label_tools

            attach_label_tools(backend, labels)
        tools.target_choice_validator = backend.audit.target_choice
        tools.pdk_update_validator = backend.audit.pdk_update_confirmation
        tools.settled_target_action = backend.audit.settled_target_action
        tools.pdk_choice_validator = backend.audit.pdk_choice
        tools.destructive_review_validator = backend.audit.destructive_decision
    except BaseException:
        close = getattr(backend, "close", None)
        if callable(close):
            close()
        raise
    return backend
