"""Server-owned identity for reconciliation decisions, including non-GUI callers."""

import getpass
import os
from contextlib import contextmanager
from contextvars import ContextVar

_caller = ContextVar("reconcile_caller", default=None)


@contextmanager
def reconcile_caller(connection, operation):
    token = _caller.set(dict(client_id=connection.identity.client_id,
                             operation_id=operation))
    try:
        yield
    finally:
        _caller.reset(token)


def audit_fields():
    return dict(actor=getpass.getuser(), uid=os.getuid(), **(_caller.get() or {
        "client_id": "local", "operation_id": "local"}))
