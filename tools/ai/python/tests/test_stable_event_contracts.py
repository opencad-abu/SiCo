"""Event value validation owns identity and commits each accepted batch atomically."""

from copy import deepcopy

import pytest

from sico.core.contracts import BoundContext, TASK_CONTRACT
from sico.core.session_events import EventCursor, SESSION_CONTRACT

CONTEXT = BoundContext('instance', 'generation', 'target', {'source': ['original']})


def event(sequence, kind, payload, task='task'):
    return dict(contract=TASK_CONTRACT, session_id='session', sequence=sequence,
                task_id=task, kind=kind, payload=payload)


def batch(*events):
    return dict(contract=SESSION_CONTRACT, session_id='session', runtime_id='runtime',
                instance_id='instance', generation='generation', events=list(events))


def started(context=None):
    return event(1, 'task.started', dict(context=context or CONTEXT.record(), text='inspect'))


def test_caller_cannot_retarget_cursor_by_editing_an_accepted_batch():
    cursor = EventCursor('session', 'runtime', CONTEXT)
    source = started()
    cursor.consume(batch(source, event(2, 'tool.started', {'id': 'call', 'name': 'read'})))
    source['payload']['context']['snapshot']['source'].append('foreign')
    source['payload']['context']['target_id'] = 'foreign'
    assert cursor.task_context == CONTEXT.record()
    original = event(3, 'session.tool_receipt', dict(
        task_id='task', id='call', name='read', source=CONTEXT.record()))
    assert cursor.consume(batch(original)) == [original]


@pytest.mark.parametrize('change', ['contract', 'target', 'snapshot', 'missing'])
def test_invalid_task_context_never_becomes_cursor_identity(change):
    source = started()
    context = source['payload']['context']
    if change == 'contract':
        context['contract'] = 'unknown.v1'
    elif change == 'target':
        context['target_id'] = '../foreign'
    elif change == 'snapshot':
        context['snapshot'] = []
    else:
        context.pop('target_id')
    cursor = EventCursor('session', 'runtime', CONTEXT)
    with pytest.raises(ValueError):
        cursor.consume(batch(source))
    assert cursor.sequence == 0 and cursor.task_context is None
    assert not cursor.native_origins.tasks


@pytest.mark.parametrize('change', ['boolean_sequence', 'contract', 'payload', 'kind', 'events'])
def test_malformed_envelopes_are_rejected_before_any_projection_commit(change):
    value = batch(started())
    if change == 'boolean_sequence':
        value['events'][0]['sequence'] = True
    elif change == 'contract':
        value['events'][0]['contract'] = 'unknown.v1'
    elif change == 'payload':
        value['events'][0]['payload'] = []
    elif change == 'kind':
        value['events'][0]['kind'] = None
    else:
        value['events'] = {}
    cursor = EventCursor('session', 'runtime', CONTEXT)
    with pytest.raises(ValueError):
        cursor.consume(value)
    assert cursor.sequence == 0 and not cursor.native_origins.tasks


def test_bad_native_tail_rolls_back_thread_task_and_call_indexes():
    cursor = EventCursor('session', 'runtime', CONTEXT)
    task = started()
    thread = event(2, 'codex.thread', {'thread_id': 'thread'})
    native = dict(thread_id='thread', turn_id='turn', item_id='item', item_type='commandExecution')
    bound = event(3, 'codex.turn.bound', {'native': native})
    tool = event(4, 'tool.started', {'id': 'call', 'name': 'read'})
    forged = event(5, 'codex.history.output', {'native': native, 'origin': {'task_id': 'foreign'}})
    with pytest.raises(ValueError, match='origin'):
        cursor.consume(batch(task, thread, bound, tool, forged))
    assert cursor.sequence == 0 and cursor.task_id == '' and not cursor.calls
    assert cursor.native_origins.thread_id is None and not cursor.native_origins.turns
    cursor.consume(batch(task, thread, bound, tool))
    forged['payload']['origin'] = cursor.native_origins.origin(native)
    cursor.consume(batch(forged))
    assert cursor.sequence == 5


def test_historical_instance_is_permitted_only_inside_fixed_history_watermark():
    old = BoundContext('old_instance', 'old_generation', 'old_target', {})
    cursor = EventCursor('session', 'runtime', CONTEXT, history_sequence=2)
    value = batch(started(old.record()), event(2, 'task.completed', {}))
    original = deepcopy(value)
    cursor.consume(value)
    assert value == original
    late = event(3, 'task.started', {'context': old.record(), 'text': 'stale'}, task='new_task')
    with pytest.raises(ValueError, match='another Virtuoso'):
        cursor.consume(batch(late))
    assert cursor.sequence == 2 and cursor.terminal
