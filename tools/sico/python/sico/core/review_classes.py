"""Blocking and supplementary review events.

A blocking review needs an explicit user answer before dependent work may
continue.  A supplementary review records an optional preference or extra
context; the model may proceed with its published recommendation without
waiting, and the host answers the native request with an empty decision.
"""

BLOCKING = "blocking"
SUPPLEMENTARY = "supplementary"
CLASSES = frozenset({BLOCKING, SUPPLEMENTARY})


def review_class(value, *, default=BLOCKING):
    """Return a validated class; absent legacy records stay blocking."""
    if value is None:
        return default
    if value not in CLASSES:
        raise ValueError("review_class must be blocking or supplementary")
    return value


def effective(declared, codex_blocking):
    """Codex's Plan-mode blocking hint can only strengthen a declaration."""
    if review_class(declared) == BLOCKING or codex_blocking is True:
        return BLOCKING
    return SUPPLEMENTARY
