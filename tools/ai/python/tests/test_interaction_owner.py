"""One waiting-state owner across durable events, cancellation and request types."""

from copy import deepcopy

import pytest
from test_codex_elicitation import running, opened, accept
from test_codex_child_input import request, answer_child


@pytest.mark.parametrize('kind,blocking', [('native', True), ('native', False), ('form', True)])
def test_dispatch_event_already_has_consumed_waiting_projection(running, monkeypatch, kind, blocking):
    backend, controller, sent = running
    backend.validate_audit_target = lambda: None
    event = request(kind, thread='thread', turn='turn')
    if kind == 'native':
        event['params']['isBlocking'] = blocking
    backend._input_request(event, 'turn')
    row = next(reversed(backend.audit.index.audits.values()))
    answer_child(backend, controller, row)
    append = backend.journal.append
    observed = []

    def record(event, payload, state):
        if event == 'workbench.audit.dispatching':
            observed.append(deepcopy(state.task.get('waiting_audits', [])))
        return append(event, payload, state)

    monkeypatch.setattr(backend.journal, 'append', record)
    assert backend.audit.deliver() == 1
    assert observed == [[]]
    assert len(sent) == 1


def test_pending_snapshot_cannot_mutate_live_request(running):
    backend, _, _ = running
    row = opened(backend)
    snapshot = backend.audit.pending
    snapshot[row['id']]['binding']['host_turn_id'] = 'foreign'
    assert backend.audit.pending[row['id']]['binding']['host_turn_id'] == 'turn'


def test_failed_open_event_does_not_leave_invisible_waiting_input(running, monkeypatch):
    backend, _, sent = running
    append = backend.journal.append

    def fail(event, payload, state):
        if event == 'workbench.audit.opened':
            raise OSError('disk full')
        return append(event, payload, state)

    monkeypatch.setattr(backend.journal, 'append', fail)
    with pytest.raises(OSError, match='disk full'):
        opened(backend)
    assert not backend.audit.pending
    assert not backend.state.task.get('waiting_audits')
    assert not sent


@pytest.mark.parametrize('kind', ['native', 'form'])
@pytest.mark.parametrize('when', ['opened', 'dispatching'])
def test_failed_publication_does_not_reopen_or_replay(running, monkeypatch, kind, when):
    backend, controller, sent = running
    backend.validate_audit_target = lambda: None
    event = request(kind, thread='thread', turn='turn')
    if when == 'dispatching':
        backend._input_request(event, 'turn')
        answer_child(backend, controller, next(reversed(backend.audit.index.audits.values())))
    append = backend.journal.append
    def fail(event_kind, payload, state):
        if event_kind == 'workbench.audit.' + when:
            raise OSError('disk full')
        return append(event_kind, payload, state)
    monkeypatch.setattr(backend.journal, 'append', fail)
    with pytest.raises(OSError, match='disk full'):
        if when == 'opened':
            backend._input_request(event, 'turn')
        else:
            backend.audit.deliver()
    monkeypatch.undo()
    backend._input_request(event, 'turn')
    assert backend.audit.deliver() == 0
    assert not sent and not backend.audit.pending and not backend.state.task.get('waiting_audits')


@pytest.mark.parametrize('kind', ['native', 'form'])
def test_consuming_one_question_preserves_sibling_in_durable_projection(running, monkeypatch, kind):
    backend, controller, _ = running
    backend.validate_audit_target = lambda: None
    backend._input_request(request(kind, thread='thread', turn='turn'), 'turn')
    row = next(reversed(backend.audit.index.audits.values()))
    backend._input_request(request('form', thread='thread', turn='turn', rpc_id=8), 'turn')
    sibling = next(reversed(backend.audit.index.audits.values()))
    answer_child(backend, controller, row)
    original, observed = backend.journal.append, []
    def record(kind, payload, state):
        if kind == 'workbench.audit.dispatching':
            observed.append(list(state.task.get('waiting_audits', [])))
        return original(kind, payload, state)
    monkeypatch.setattr(backend.journal, 'append', record)
    assert backend.audit.deliver() == 1
    assert observed == [[sibling['id']]]
    assert list(backend.audit.pending) == backend.state.task['waiting_audits'] == [sibling['id']]


def host_answer(backend):
    event = request('native', thread='thread', turn='turn')
    current = backend.workbench.active()
    row = {'id': 'a_host', 'title': 'PDK', 'questions': event['params']['questions'],
           'context': backend.state.context.record(), 'task_id': backend.state.task['id'],
           'work_id': current['work_id'], 'stage_id': current['stage_id'],
           'evidence': [{'id': current['source'],
                         'digest': backend.audit.index.data[current['source']]['digest']}],
           'pdk_update_ref': 'update:test'}
    row['questions'][0]['id'] = 'pdk_data_update'
    backend.audit.emit('prepared', row)
    backend.audit.open_host(row, 'pdk_data_update')
    backend.audit.answer(row['id'], {'pdk_data_update': {'choice': '全局', 'text': ''}},
                         'host-reply', 'user', row['task_id'])
    backend.validate_audit_target = lambda: None
    assert len(backend.audit.deliver_host()) == 1
    return event, backend.audit.index.audits[row['id']]


@pytest.mark.parametrize('boundary', ['none', 'resolved', 'connection', 'cancel'])
def test_late_host_binding_is_one_shot_and_revalidated(running, monkeypatch, boundary):
    from types import SimpleNamespace
    from test_codex_elicitation import resolved
    backend, _, sent = running
    event, row = host_answer(backend)
    evidence_id = row['reply_data']
    original = backend.audit.emit
    def emit(kind, payload):
        original(kind, payload)
        if kind == 'bound':
            if boundary == 'resolved':
                backend._notification(resolved())
            elif boundary == 'connection':
                backend.runtime.rpc = SimpleNamespace(send=lambda frame, **kw: sent.append(frame))
                backend.input_connection()
            elif boundary == 'cancel':
                backend.cancelled = lambda: True
    monkeypatch.setattr(backend.audit, 'emit', emit)
    backend._input_request(event, 'turn')
    if boundary == 'none':
        backend._input_request(event, 'turn')
        assert len(sent) == 1 and row['status'] == 'answered'
        assert row['reply_data'] == evidence_id  # one human decision, two delivery routes
    else:
        assert not sent and row['status'] == 'unconfirmed'
    assert not backend.audit.pending and not backend.state.task.get('waiting_audits')


@pytest.mark.parametrize('kind', ['native', 'form'])
@pytest.mark.parametrize('phase', ['prepared', 'opened'])
def test_resolution_during_registration_never_leaves_waiting_item(running, monkeypatch, kind, phase):
    from test_codex_elicitation import resolved
    backend, _, sent = running
    original = backend.audit.emit
    def emit(action, payload):
        original(action, payload)
        if action == phase:
            backend._notification(resolved())
    monkeypatch.setattr(backend.audit, 'emit', emit)
    event = request(kind, thread='thread', turn='turn')
    backend._input_request(event, 'turn')
    backend._input_request(event, 'turn')
    assert not sent and not backend.audit.pending and not backend.state.task.get('waiting_audits')


@pytest.mark.parametrize('boundary', ['thread', 'turn'])
def test_native_dispatch_cannot_adopt_scope_changed_during_publication(running, monkeypatch, boundary):
    backend, controller, sent = running
    backend.validate_audit_target = lambda: None
    backend._input_request(request('native', thread='thread', turn='turn'), 'turn')
    row = next(reversed(backend.audit.index.audits.values()))
    answer_child(backend, controller, row)
    original = backend.audit.emit
    def emit(kind, payload):
        original(kind, payload)
        if kind == 'dispatching':
            if boundary == 'thread':
                from codex_thread_fixture import bind_thread
                bind_thread(backend, 'foreign')
            else:
                backend.steering.end()
                backend.steering.open('foreign')
    monkeypatch.setattr(backend.audit, 'emit', emit)
    assert backend.audit.deliver() == 0
    assert not sent and row['status'] == 'unconfirmed'


def test_reconnect_cannot_rebind_an_answered_host_mailbox(running):
    from types import SimpleNamespace
    backend, _, sent = running
    event, row = host_answer(backend)
    assert backend.audit.requests.delivered_hosts() == (row['id'],)
    backend.runtime.rpc = SimpleNamespace(send=lambda frame, **kw: sent.append(frame))
    backend.input_connection()
    assert not backend.audit.requests.delivered_hosts()
    backend._input_request(event, 'turn')
    assert not sent
    assert backend.audit.pending and row['id'] not in backend.audit.pending


def test_close_discards_host_delivery_receipt(running):
    backend, _, _ = running
    host_answer(backend)
    backend.runtime.close = lambda: None
    backend.close()
    assert not backend.audit.requests.delivered_hosts()
    assert not backend.audit.pending and not backend.state.task.get('waiting_audits')
