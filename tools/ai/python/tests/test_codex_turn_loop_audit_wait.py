"""A terminated native turn retains only business questions, with one fresh delivery."""

import pytest

from audit_turn_fixture import AuditTurn
from sico.codex.rpc import RpcError, RpcRejected
from sico.storage.workbench import WorkbenchIndex


@pytest.fixture
def turn(tmp_path):
    harness = AuditTurn(tmp_path)
    try:
        yield harness
    finally:
        harness.close()


@pytest.mark.parametrize('status', ['interrupted', 'failed', 'completed'])
@pytest.mark.parametrize('host', [False, True])
def test_ended_turn_answer_starts_one_new_turn_and_completes(turn, status, host):
    turn.status = status
    turn.on_start = lambda: turn.question(host=host)
    def wait():
        row = turn.rows[0]
        assert row['status'] == 'pending'
        assert turn.backend.audit.pending[row['id']]['rpc_id'] is None
        if turn.polls == 1:
            turn.now += 100000  # Waiting for a user does not consume the idle budget.
            return
        assert turn.backend.state.task['activity']['phase'] == 'waiting_user'
        assert turn.answer() and not turn.answer()  # A duplicate is not another delivery.
    turn.on_wait = wait
    turn.on_continue = lambda: pytest.fail('answer not dispatching') if (
        turn.rows[0]['status'] != 'dispatching') else None
    events = turn.run()
    assert events[-1]['kind'] == 'task.completed', turn.backend.state.task['diagnostic']
    assert len(turn.starts()) == 2 and not turn.sent
    assert 'Inspect' in turn.starts()[1]['input'][0]['text']
    assert turn.rows[0]['status'] == 'answered' and not turn.backend.audit.pending
    bound = [e['payload']['native']['turn_id'] for e in events if e['kind'] == 'codex.turn.bound']
    assert bound == ['turn', 'continued']
    assert len([e for e in events if e['kind'] == 'workbench.audit.resumed']) == 1
    replayed = WorkbenchIndex(turn.journal).sync().audits[turn.rows[0]['id']]
    assert replayed['status'] == 'answered' and replayed['binding'].get('continuation') == (not host or None)


def test_old_resolution_and_duplicate_request_do_not_remove_retained_question(turn):
    turn.on_start = turn.question
    def wait():
        row = turn.rows[0]
        turn.backend.inputs.resolved({'method': 'serverRequest/resolved', 'params': {
            'threadId': 'thread', 'requestId': 41}})
        turn.backend.audit.open({'id': 41, 'params': {
            'threadId': 'thread', 'turnId': 'turn', 'itemId': 'scope',
            'isBlocking': True, 'questions': row['questions']}})
        assert row['status'] == 'pending' and len(turn.backend.audit.pending) == 1
        turn.answer()
    turn.on_wait = wait
    assert turn.run()[-1]['kind'] == 'task.completed'
    assert len(turn.starts()) == 2 and not turn.sent


@pytest.mark.parametrize('boundary', ['cancel', 'source', 'connection', 'withdraw', 'evidence'])
def test_wait_boundary_cannot_start_another_turn(turn, boundary):
    turn.on_start = turn.question
    def wait():
        if boundary == 'cancel':
            turn.cancel = True
        elif boundary == 'source':
            turn.backend.stale, turn.backend.stale_reason = True, 'target changed'
        elif boundary == 'connection':
            turn.backend.runtime.rpc = object()
        elif boundary == 'withdraw':
            turn.backend.audit.end_inputs('request withdrawn', send=False)
        else:
            row = turn.rows[0]
            row['evidence'][0]['digest'] = 'changed'
            turn.answer()
    turn.on_wait = wait
    turn.run()
    assert len(turn.starts()) == 1 and not turn.sent
    assert not turn.backend.audit.pending
    assert turn.backend.state.task['status'] in {'cancelled', 'needs_reconcile'}
    assert 'consumer disconnected' not in turn.backend.state.task['diagnostic']


@pytest.mark.parametrize('failure,status', [(RpcRejected('rejected'), 'invalid'),
    (RpcError('lost receipt'), 'unconfirmed'), (OSError('broken connection'), 'unconfirmed')])
def test_uncertain_or_rejected_continuation_is_never_resent(turn, failure, status):
    turn.on_start = turn.question
    turn.on_wait = turn.answer
    def fail():
        raise failure
    turn.on_continue = fail
    turn.run()
    assert turn.rows[0]['status'] == status
    assert len(turn.starts()) == 2 and not turn.sent
    assert not turn.backend.audit.pending and turn.backend.state.task['status'] == 'needs_reconcile'
    assert not any(e['kind'] == 'workbench.audit.resumed' for e in turn.journal.events())


def test_mixed_host_native_answers_are_batched_without_losing_the_first(turn):
    def start():
        turn.question(host=True)
        # Native input is independently registered; preparation intentionally remains serial.
        turn.backend.audit.open({'id': 42, 'params': {
            'threadId': 'thread', 'turnId': 'turn', 'itemId': 'second', 'isBlocking': True,
            'questions': [{'id': 'second', 'header': 'Second', 'question': 'Inspect second?',
                          'options': [{'label': 'Inspect', 'description': 'Read only'}]}]}})
        turn.rows.append(next(reversed(turn.backend.audit.index.audits.values())))
    turn.on_start = start
    turn.on_wait = lambda: turn.answer(turn.rows[min(turn.polls - 1, 1)])
    assert turn.run()[-1]['kind'] == 'task.completed'
    assert len(turn.starts()) == 2 and not turn.sent
    assert all(row['id'] in turn.starts()[1]['input'][0]['text'] for row in turn.rows)
    assert all(row['status'] == 'answered' for row in turn.rows)


@pytest.mark.parametrize('kind', ['approval', 'elicitation', 'child'])
def test_turn_scoped_permissions_and_children_do_not_become_continuations(turn, kind):
    def start():
        if kind == 'approval':
            from test_codex_access import request
            turn.backend._input_request(request(), 'turn')
        elif kind == 'elicitation':
            from test_codex_elicitation import event
            turn.backend._input_request(event(), 'turn')
        else:
            from test_codex_child_input import register, request
            register(turn.backend)
            turn.backend._input_request(request('native'), 'turn')
        turn.rows.append(next(reversed(turn.backend.audit.index.audits.values())))
    turn.on_start = start
    turn.on_wait = lambda: pytest.fail('turn-scoped input survived termination')
    turn.run()
    assert turn.rows[0]['status'] == 'invalid' and not turn.backend.audit.pending
    assert len(turn.starts()) == 1
    assert not any('answers' in item.get('result', {}) for item in turn.sent)


def test_unconfirmed_native_operation_prevents_automatic_continuation(turn):
    def start():
        turn.question()
        turn.backend.native.completed[('thread', 'turn', 'external')] = 'needs_reconcile'
    turn.on_start = start
    turn.run()
    assert len(turn.starts()) == 1 and not turn.sent
    assert turn.backend.state.task['status'] == 'needs_reconcile'
    assert not turn.backend.audit.pending


@pytest.mark.parametrize('phase', ['dispatching', 'accepted'])
@pytest.mark.parametrize('boundary', ['cancel', 'source', 'connection'])
def test_delivery_boundary_preserves_uncertainty_without_replay(turn, monkeypatch, phase, boundary):
    turn.on_start = turn.question
    turn.on_wait = turn.answer
    def change():
        if boundary == 'cancel':
            turn.cancel = True
        elif boundary == 'source':
            turn.backend.stale, turn.backend.stale_reason = True, 'changed during delivery'
        else:
            from types import SimpleNamespace
            turn.backend.runtime.rpc = SimpleNamespace(send=lambda *a, **kw: None)
    if phase == 'accepted':
        turn.on_continue = change
    else:
        original = turn.backend.audit.emit
        def emit(action, payload):
            original(action, payload)
            if action == 'dispatching':
                change()
        monkeypatch.setattr(turn.backend.audit, 'emit', emit)
    turn.run()
    assert len(turn.starts()) == (2 if phase == 'accepted' else 1)
    assert turn.rows[0]['status'] == ('unconfirmed' if phase == 'accepted' else 'invalid')
    assert not turn.backend.audit.pending and not turn.sent


@pytest.mark.parametrize('phase', ['bound', 'dispatching'])
def test_failed_durable_transition_cannot_resend_answer(turn, monkeypatch, phase):
    turn.on_start = turn.question
    turn.on_wait = turn.answer
    original = turn.backend.audit.emit
    def emit(action, payload):
        if action == phase and (phase != 'bound' or payload.get('continuation')):
            raise OSError('synthetic journal failure')
        original(action, payload)
    monkeypatch.setattr(turn.backend.audit, 'emit', emit)
    turn.run()
    assert len(turn.starts()) == 1 and not turn.sent
    assert not turn.backend.audit.pending
    assert turn.backend.state.task['status'] == 'needs_reconcile'


def test_answer_received_at_terminal_boundary_is_not_lost(turn):
    turn.on_start = turn.question
    original = turn.next
    def next_event():
        event = original()
        if event.get('method') == 'turn/completed' and turn.phase == 'waiting':
            turn.answer()
        return event
    turn.next = next_event
    assert turn.run()[-1]['kind'] == 'task.completed'
    assert len(turn.starts()) == 2 and not turn.sent
    assert turn.rows[0]['status'] == 'answered'


@pytest.mark.parametrize('host', [False, True])
@pytest.mark.parametrize('recovery', ['valid', 'invalid', 'cancel'])
def test_unconfirmed_target_retains_answer_without_starting_turn(turn, monkeypatch, host, recovery):
    from sico.core.target_validation import TargetUnconfirmed
    from sico.core.contracts import NeedsReconcile

    turn.on_start = lambda: turn.question(host=host)
    original = turn.backend.validate_audit_target
    def validate():
        if turn.polls < 3:
            raise TargetUnconfirmed('temporary source read')
        if recovery == 'invalid':
            raise NeedsReconcile('confirmed changed source')
        return original()
    monkeypatch.setattr(turn.backend, 'validate_audit_target', validate)
    def wait():
        if turn.polls == 1:
            turn.answer()
        else:
            assert turn.rows[0]['status'] == 'answer_received'
            assert len(turn.starts()) == 1 and not turn.sent
            assert not turn.backend.stale
            if recovery == 'cancel':
                turn.cancel = True
    turn.on_wait = wait
    events = turn.run()
    if recovery == 'valid':
        assert events[-1]['kind'] == 'task.completed'
        assert len(turn.starts()) == 2 and turn.rows[0]['status'] == 'answered'
    else:
        assert len(turn.starts()) == 1 and turn.rows[0]['status'] == 'invalid'
    assert not turn.backend.audit.pending and not turn.sent
