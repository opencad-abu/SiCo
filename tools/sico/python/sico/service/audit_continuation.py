"""Retain business questions at a native turn boundary and submit one continuation."""

import json
from copy import deepcopy

from ..codex.rpc import RpcError, RpcRejected
from ..core.contracts import NeedsReconcile
from ..core.target_validation import TargetUnconfirmed
from .audit_records import host_content


def retain(audit, turn_id):
    """A terminal turn releases its RPC IDs, never its approvals or child inputs."""
    with audit.runtime.lock:
        audit.index.sync()
        scope = audit.runtime.snapshot()
        for key, pending in audit.pending.items():
            if pending['rpc_id'] is None or pending.get('child_thread_id'):
                continue
            binding = pending.get('binding') or {}
            row = audit.index.audits[key]
            if (pending.get('source') != 'native' or not pending.get('blocking', True)
                    or binding.get('threadId') != scope.thread_id
                    or binding.get('turnId') != turn_id
                    or row['status'] not in {'pending', 'answer_received'}):
                audit.end_inputs('原回合已结束，此交互不能转入后续回合', keys=[key], send=False)
                continue
            audit.native_current(key)
            retain_question(audit, key, pending, scope)


def retain_question(audit, key, pending, scope):
    retained = dict(rpc_id=None, binding=None, blocking=True, source='host',
                    review_class=pending['review_class'],
                    host_connection_id=audit.requests.connection_id,
                    host_thread_id=scope.thread_id)
    audit.requests.retain(key, retained, lambda: audit.emit('bound', {
        'id': key, 'source': 'host', 'continuation': True, **pending['binding']}))


def retain_resolved(audit, key, pending):
    """RPC resolution may precede turn/completed; retain only an established question."""
    scope = audit.runtime.snapshot()
    audit.index.sync()
    row = audit.index.audits[key]
    binding = pending.get('binding') or {}
    # Registration and dispatch races remain terminal. Only a question already
    # published in this live root turn can survive closure of its native RPC.
    if (not scope.admitted or pending.get('source') != 'native'
            or pending.get('child_thread_id') or not pending.get('blocking', True)
            or row['status'] not in {'pending', 'answer_received'}
            or not row.get('binding') or row['task_id'] != scope.task_id
            or row['context'] != scope.context or not scope.turn_id
            or binding.get('threadId') != scope.thread_id
            or binding.get('turnId') != scope.turn_id
            or not audit.requests.is_current()
            or binding.get('connection_id') != audit.requests.connection_id):
        return False
    retain_question(audit, key, pending, scope)
    return True


def content(row):
    """Preserve exact question/answer association as user data in the next turn."""
    special = host_content(row)
    return special if special is not None else json.dumps({
        'audit_id': row['id'], 'title': row['title'],
        'questions': row['questions'], 'answers': row['reply']['answers'],
    }, ensure_ascii=False, allow_nan=False)


def current(audit, scope, pending, row):
    if (not scope.admitted or audit.runtime.snapshot() != scope
            or row['task_id'] != scope.task_id or row['context'] != scope.context
            or pending['rpc_id'] is not None or pending.get('source') != 'host'
            or pending.get('child_thread_id') or 'native_approval' in row or 'elicitation' in row
            or not audit.requests.is_current()
            or (pending.get('host_connection_id') and (
                pending['host_connection_id'] != audit.requests.connection_id
                or pending['host_thread_id'] != scope.thread_id))):
        raise ValueError('Retained review no longer belongs to this task or connection')
    audit.index.verify_evidence(row['evidence'])


def prepare(audit):
    """Claim all ready answers once, after target and evidence validation."""
    with audit.runtime.lock:
        audit.index.sync()
        scope = audit.runtime.snapshot()
        pending = audit.pending
        if not pending:
            raise NeedsReconcile('等待的问题已结束，未启动新的回合；请核对后继续')
        rows = {key: deepcopy(audit.index.audits[key]) for key in pending}
        for key, item in pending.items():
            current(audit, scope, item, rows[key])
        if any(row['status'] != 'answer_received' for row in rows.values()):
            return None
    audit.runtime.validate_target()
    with audit.runtime.lock:
        audit.index.sync()
        if audit.pending != pending:
            raise NeedsReconcile('等待的问题已变化，答复未交付')
        for key, item in pending.items():
            current(audit, scope, item, rows[key])
            if audit.index.audits[key] != rows[key]:
                raise NeedsReconcile('答复记录已变化，未启动新的回合')
        text = '用户对原任务待答复问题的答复：\n' + '\n'.join(content(row) for row in rows.values())
        for key, row in rows.items():
            audit.requests.resolve(key, lambda: audit.emit('dispatching', {
                'id': key, 'reply_id': row['reply']['reply_id']}))
        return scope, rows, text, audit.requests.connection


def terminal(audit, rows, action, reason):
    with audit.runtime.lock:
        for key in rows:
            if audit.index.audits[key]['status'] == 'dispatching':
                audit.emit(action, {'id': key, 'reason': reason})


def submit(audit, previous_turn):
    """No native reply is sent after termination; accept a fresh turn or stop."""
    poll = getattr(audit.requests.connection, 'poll_notifications', None)
    if callable(poll):
        poll()
    if not audit.runtime.admitted():
        return None
    try:
        claim = prepare(audit)
    except TargetUnconfirmed as exc:
        audit.runtime.activity('waiting_user', str(exc))
        return None
    except (ValueError, OSError, TypeError, KeyError, NeedsReconcile) as exc:
        audit.end_inputs('目标、证据或交互已变化，答复未交付', send=False)
        raise NeedsReconcile('目标、证据或交互已变化，未启动新的回合') from exc
    if claim is None:
        return None
    scope, rows, text, connection = claim
    with audit.runtime.lock:
        if (audit.runtime.snapshot() != scope or not audit.requests.is_current()
                or audit.requests.connection is not connection
                or any(audit.index.audits[key]['status'] != 'dispatching' for key in rows)):
            terminal(audit, rows, 'invalidated', '发送前来源已变化，答复未交付')
            raise NeedsReconcile('发送前来源已变化，未启动新的回合')
    try:
        result = connection.request('turn/start', {
            'threadId': scope.thread_id, 'input': [{'type': 'text', 'text': text}],
        }, cancelled=lambda: not audit.runtime.admitted())
        turn_id = result['turn']['id']
        if not isinstance(turn_id, str) or not turn_id or turn_id == previous_turn:
            raise RpcError('Invalid continuation turn receipt; no replay')
    except RpcRejected:
        terminal(audit, rows, 'invalidated', '后续回合被服务拒绝，答复未交付')
        raise
    except (OSError, ValueError, RuntimeError, TypeError, KeyError):
        terminal(audit, rows, 'unconfirmed', '后续回合接收结果未确认；答复不会自动重发')
        raise
    with audit.runtime.lock:
        if (audit.runtime.snapshot() != scope or not audit.requests.is_current()
                or audit.requests.connection is not connection
                or any(audit.index.audits[key]['status'] != 'dispatching' for key in rows)):
            terminal(audit, rows, 'unconfirmed', '发送期间来源已变化；答复交付未确认，不会自动重发')
            raise NeedsReconcile('后续回合接收后来源已变化；请核对结果，不会自动重跑')
        for key, row in rows.items():
            audit.emit('resumed', {'id': key, 'reply_id': row['reply']['reply_id'],
                                  'delivery': 'turn_accepted', 'turn_id': turn_id})
        audit.runtime.activity('waiting_model', '答复已接收，等待后续结果')
    return turn_id
