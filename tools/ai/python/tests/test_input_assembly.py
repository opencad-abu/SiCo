"""Input composition preserves current ownership without capturing stale state."""

from dataclasses import FrozenInstanceError
from threading import RLock
from types import SimpleNamespace

import pytest

from sico.codex.input_assembly import capture, child_inputs, request_owner
from sico.core.input_scope import InputScope


@pytest.fixture
def task():
    context = {'target': {'cell': 'original'}}
    loop = SimpleNamespace(
        lock=RLock(), state=SimpleNamespace(
            task={'id': 'first', 'status': 'executing', 'origin': 'chat'},
            context=SimpleNamespace(record=lambda: context)),
        active=True, cancelled=lambda: False, stale=False,
        turn_options=SimpleNamespace(selected={'access': {'approval': 'on-request'}}),
        _event=lambda *a: None,
    )
    return loop, context


def test_scope_is_detached_and_each_capture_reads_current_task(task):
    loop, context = task
    first = capture(loop)
    assert isinstance(first, InputScope) and first.executing and first.admitted
    assert first.thread_id == first.turn_id == ''  # Python task has no Codex thread.
    with pytest.raises(FrozenInstanceError):
        first.task_id = 'foreign'
    first.context['target']['cell'] = 'caller edit'
    first.access['access']['approval'] = 'never'
    assert context['target']['cell'] == 'original'
    assert loop.turn_options.selected['access']['approval'] == 'on-request'
    loop.state.task = {'id': 'second', 'status': 'completed', 'origin': 'startup',
                       'turn_options': {'access': {'approval': 'never'}}}
    loop.steering = SimpleNamespace(turn_id='turn-2')
    loop.thread_id = 'thread-2'
    second = capture(loop)
    assert (second.task_id, second.origin, second.thread_id, second.turn_id) == (
        'second', 'startup', 'thread-2', 'turn-2')
    assert not second.executing and second.access['access']['approval'] == 'never'
    second.access.clear()
    assert loop.state.task['turn_options']['access']['approval'] == 'never'


@pytest.mark.parametrize('change', ['inactive', 'cancelled', 'stale'])
def test_scope_admission_tracks_live_task_state(task, change):
    loop, _ = task
    assert capture(loop).admitted
    if change == 'inactive':
        loop.active = False
    elif change == 'cancelled':
        loop.cancelled = lambda: True
    else:
        loop.stale = True
    assert not capture(loop).admitted


def test_owner_resolves_late_audit_and_replacement_task_connection(task):
    loop, _ = task
    # These callbacks must not be evaluated during backend construction.
    loop.inputs = request_owner(loop)
    loop.orchestration = SimpleNamespace(children={})
    loop._record_subagent = lambda item: None
    loop.child_inputs = child_inputs(loop)
    assert not loop.inputs.snapshot() and loop.child_inputs.active() == []
    assert loop.inputs.connect(required=False) == ''
    loop.inputs.open('host', {'rpc_id': None}, lambda: None)
    original = loop.state.task
    assert original['waiting_audits'] == ['host']
    loop.inputs.finish()
    loop.state.task = {'id': 'next'}
    loop.runtime = SimpleNamespace(rpc=object())
    first_id = loop.inputs.connect()
    withdrawals = []
    loop.audit = SimpleNamespace(end_inputs=lambda *a, **kw: withdrawals.append((a, kw)))
    loop.inputs.open('native', {'rpc_id': 7}, lambda: None)
    loop.runtime.rpc = object()
    assert loop.inputs.connect() != first_id
    assert len(withdrawals) == 1 and withdrawals[0][1] == {'send': False}
    assert not loop.inputs.snapshot()
    assert 'waiting_audits' not in original and 'waiting_audits' not in loop.state.task


def test_workbench_reuses_codex_owner_and_python_fallback_has_no_connection(tmp_path):
    from sico.codex.backend import CodexBackend
    from sico.core.contracts import BoundContext
    from sico.service.python_backend import PythonBackend
    from sico.core.tools import ToolRegistry
    from sico.service.workbench import attach_workbench
    from sico.storage.journal import SessionJournal

    context = BoundContext('instance', 'generation', 'window', {'valid': True})
    for cls in (CodexBackend, PythonBackend):
        with SessionJournal(tmp_path, cls.__name__) as journal:
            loop = cls(None, ToolRegistry(), journal, context)
            try:
                owner = getattr(loop, 'inputs', None)
                loop.workbench = attach_workbench(loop)
                if owner is not None:
                    assert loop.audit.requests is owner
                else:
                    assert loop.audit.requests.connect(required=False) == ''
                scope = loop.audit.runtime.snapshot()
                assert scope.context == context.record() and not scope.admitted
            finally:
                if isinstance(loop, CodexBackend):
                    loop.close()


def test_legacy_input_exports_are_exact_objects():
    from sico.service import audit_runtime
    from sico.codex import input_assembly

    assert audit_runtime.InputScope is InputScope
    for name in ('capture', 'request_owner', 'child_inputs'):
        assert getattr(audit_runtime, name) is getattr(input_assembly, name)
