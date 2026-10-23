"""Keep a controller's immutable task view aligned with out-of-band changes."""

from .published import freeze


def refresh(owner):
    """Refresh ``owner._task``; return whether a new task revision was published.

    Controller commands and native RPC callbacks mutate the task from threads
    that do not yield worker events. The event publisher calls this adapter so
    the next state snapshot contains the current activity and audit wait set.
    """
    task = freeze(owner.loop.state.task)
    if task == owner._task:
        return False
    owner._task = task
    owner.version += 1
    return True
