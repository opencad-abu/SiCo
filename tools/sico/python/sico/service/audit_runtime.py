"""Adapt task operations to the audit service with retained input aliases."""

from dataclasses import dataclass
from copy import deepcopy
from typing import Callable

# Compatibility exports; task owners compose inputs through codex.input_assembly.
from ..core.input_scope import InputScope as InputScope
from ..codex.input_assembly import (
    capture as capture, request_owner as request_owner, child_inputs as child_inputs,
)


@dataclass(frozen=True)
class AuditRuntime:
    lock: object
    snapshot: Callable
    admitted: Callable
    activity: Callable
    validate_target: Callable
    invalidate: Callable
    child_current: Callable
    child_scope: Callable
    approval_changes: Callable
    require: Callable


def assemble(loop):
    def invalidate(reason):
        loop.stale, loop.stale_reason = True, reason

    def approval_changes(key):
        import json

        loop.support.require("native_approval")
        item = loop.native.pending.get(key, {}).get('input', {})
        if item.get('truncated'):
            asset = item['artifact']
            item = json.loads(loop.journal.artifact_text(asset['path'], asset['sha256']))
        return deepcopy(item.get('changes'))

    def child_current(*args, **kwargs):
        loop.support.require("child_tasks")
        return loop.child_inputs.current(*args, **kwargs)

    def child_scope(child):
        loop.support.require("child_tasks")
        return loop.child_inputs.scope(child)

    return AuditRuntime(
        loop.lock, lambda: capture(loop),
        lambda: loop.active and not loop.cancelled() and not loop.stale,
        lambda *a, **kw: loop._activity(*a, **kw), lambda: loop.validate_audit_target(), invalidate,
        child_current, child_scope, approval_changes, loop.support.require,
    )
