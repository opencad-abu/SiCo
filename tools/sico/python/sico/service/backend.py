"""Select a task engine explicitly; the Python model loop has been retired."""

from ..codex.settings import CodexSettings


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
        raise ValueError(
            "The Python model loop has been retired; configure a Codex provider"
        )
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
