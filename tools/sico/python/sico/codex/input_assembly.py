"""Bind live input owners to current task and child lifecycle callbacks."""

from copy import deepcopy

from ..core.input_scope import InputScope
from .input_requests import InputRequests
from .child_input import ChildInputs


def capture(loop):
    task = loop.state.task
    return InputScope(
        task.get('id', ''), deepcopy(loop.state.context.record()), getattr(loop, 'thread_id', ''),
        getattr(getattr(loop, 'steering', None), 'turn_id', ''), task.get('origin', ''),
        deepcopy(task.get('turn_options') or getattr(getattr(loop, 'turn_options', None), 'selected', {})),
        task.get('status') == 'executing',
        loop.active and not loop.cancelled() and not loop.stale,
    )


def request_owner(loop):
    return InputRequests(
        lock=loop.lock, task=lambda: loop.state.task,
        rpc=lambda: getattr(getattr(loop, 'runtime', None), 'rpc', None),
        thread=lambda: getattr(loop, 'thread_id', ''),
        child_current=lambda *args, **kw: loop.child_inputs.current(*args, **kw),
        emit=loop._event,
        withdraw=lambda *args, **kw: loop.audit.end_inputs(*args, **kw) if hasattr(loop, 'audit') else None,
    )


def child_inputs(loop):
    return ChildInputs(
        lock=loop.lock, snapshot=lambda: capture(loop), requests=loop.inputs,
        children=loop.orchestration.children, emit=loop._event,
        withdraw=lambda *a, **kw: loop.audit.withdraw_inputs(*a, **kw) if hasattr(loop, 'audit') else [],
        refresh_history=lambda: loop.native_history.refresh(),
        known_turn=lambda thread, turn: (thread, turn) in loop.native_history.origins.turns,
        subagent=loop._record_subagent,
    )
