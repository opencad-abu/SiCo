"""Interaction owners operate independently of a backend or persistence service."""

from dataclasses import replace
from threading import RLock, Thread
from types import SimpleNamespace

import pytest

from sico.codex.child_input import ChildInputs
from sico.codex.elicitation import Elicitations
from sico.codex.input_delivery import InputDelivery
from sico.codex.input_requests import InputRequests
from sico.core.input_scope import InputScope
from sico.service.audit_runtime import AuditRuntime
from sico.service.audit import AuditService


def fail():
    raise OSError('disk full')


@pytest.fixture
def port():
    task, sent, events = {'id': 'task'}, [], []
    state = SimpleNamespace(rpc=SimpleNamespace(send=lambda frame, **kw: sent.append(frame)))
    lock = RLock()
    owner = InputRequests(lock=lock, task=lambda: task, rpc=lambda: state.rpc,
                          thread=lambda: 'root', child_current=lambda *a, **kw: None,
                          emit=lambda *a: events.append(a), withdraw=lambda *a, **kw: owner.finish())
    owner.connect()
    return owner, state, task, lock, sent, events


@pytest.mark.parametrize('operation', ['open', 'bind', 'resolve', 'cancel'])
def test_failed_publication_drops_only_its_mailbox(port, operation):
    owner, _, task, _, _, _ = port
    owner.open('sibling', {'rpc_id': 2}, lambda: None)
    if operation != 'open':
        owner.open('item', {'rpc_id': None}, lambda: None)
    with pytest.raises(OSError, match='disk full'):
        if operation in {'open', 'bind'}:
            getattr(owner, operation)('item', {'rpc_id': 1}, fail)
        else:
            getattr(owner, operation)('item', fail)
    assert list(owner.snapshot()) == task['waiting_audits'] == ['sibling']


def test_claim_cannot_cross_connection_and_connection_event_failure_is_closed(port):
    owner, state, _, _, _, _ = port
    claim = owner.check(7, 'root', {'question': 'original'})
    state.rpc = object()
    owner.connect()
    with pytest.raises(ValueError, match='connection changed'):
        owner.open('old', {'rpc_id': 7}, lambda: None, claim=claim)
    assert not owner.snapshot()
    owner._emit = lambda *args: fail()
    state.rpc = object()
    with pytest.raises(OSError):
        owner.connect()
    assert not owner.is_current() and owner.connection_id == ''


def test_finish_inside_publication_cannot_resurrect_host_response(port):
    owner, _, task, _, _, _ = port
    owner.open('host', {'rpc_id': None}, lambda: None)
    owner.resolve('host', owner.finish, host_response={'q': {'answers': ['yes']}})
    assert not owner.delivered_hosts() and not owner.snapshot() and 'waiting_audits' not in task


def test_concurrent_consumers_publish_once(port):
    owner, _, task, _, _, _ = port
    published, outcomes = [], []
    owner.open('item', {'rpc_id': 1}, lambda: None)
    def consume():
        outcomes.append(owner.resolve('item', lambda: published.append('dispatch')))
    threads = [Thread(target=consume) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    assert len(published) == 1 and sum(value is not None for value in outcomes) == 1
    assert 'waiting_audits' not in task


def test_lost_connection_withdraws_host_and_native_and_rotates_once(port):
    owner, state, task, _, _, events = port
    original = owner.connection_id
    owner.open('host', {'rpc_id': None}, lambda: None)
    owner.open('native', {'rpc_id': 1}, lambda: None)
    state.rpc = None
    assert owner.connect(required=False) == ''
    assert not owner.snapshot() and 'waiting_audits' not in task
    state.rpc = object()
    assert owner.connect() != original
    assert owner.connect() == owner.connection_id
    assert len(events) == 2


def runtime_for(lock, scope, events):
    from sico.core.backend_support import CODEX_SUPPORT

    return AuditRuntime(lock, lambda: scope[0], lambda: scope[0].admitted,
                        lambda *args: events.append(args), lambda: None,
                        lambda reason: None, lambda *a, **kw: None,
                        lambda child: {}, lambda key: None, CODEX_SUPPORT.require)


def test_delivery_uses_narrow_port_and_sends_once_without_state_lock(port):
    owner, state, _, lock, sent, events = port
    scope = [InputScope('task', {}, 'root', 'turn', 'chat', {}, True, True)]
    runtime = runtime_for(lock, scope, events)
    statuses = ['dispatching']
    observed = []
    def send(frame, **kwargs):
        def acquire():
            with lock:
                observed.append('unlocked')
        thread = Thread(target=acquire)
        thread.start()
        thread.join(2)
        assert not thread.is_alive()
        sent.append(frame)
    state.rpc.send = send
    pending = {'rpc_id': 7, 'binding': {'connection_id': owner.connection_id, 'threadId': 'root'}}
    row = {'task_id': 'task', 'context': {}, 'reply': {'reply_id': 'r'}}
    def emit(kind, payload):
        statuses[0] = 'answered' if kind == 'resumed' else kind
    delivery = InputDelivery('a', pending, owner.connection, {'answer': True}, emit,
                              runtime=runtime, requests=owner, row=row, status=lambda: statuses[0])
    assert delivery.send() == 1 and delivery.send() == 0
    assert observed == ['unlocked'] and len(sent) == 1


def test_handlers_compose_without_backend_and_scope_mutation_cancels_delivery(port):
    owner, _, _, lock, sent, events = port
    scope = [InputScope('task', {}, 'root', 'turn', 'chat', {}, True, True)]
    runtime = runtime_for(lock, scope, events)
    index = SimpleNamespace(audits={})
    audit = AuditService(index=index, active_work=lambda: {}, evidence=lambda *a: [],
                         publish=lambda *a: events.append(a), runtime=runtime, requests=owner)
    assert isinstance(audit.elicitations, Elicitations)
    children = ChildInputs(lock=lock, snapshot=runtime.snapshot, requests=owner, children={},
                            emit=lambda *a: None, withdraw=lambda *a, **kw: [],
                            refresh_history=lambda: None, known_turn=lambda *a: False,
                            subagent=lambda *a: None)
    assert children.active() == []
    statuses = ['dispatching']
    delivery = InputDelivery('a', {'rpc_id': 7, 'binding': {
        'connection_id': owner.connection_id, 'threadId': 'root'}}, owner.connection, {},
        lambda kind, payload: statuses.append(kind), runtime=runtime, requests=owner,
        row={'task_id': 'task', 'context': {}, 'reply': {'reply_id': 'r'}},
        status=lambda: statuses[0])
    scope[0] = replace(scope[0], thread_id='foreign')
    assert delivery.send() == 0 and not sent and statuses[-1] == 'unconfirmed'
    for handler in (audit, audit.elicitations, children, delivery):
        assert not {'loop', 'backend', 'state', 'workbench'} & vars(handler).keys()


def test_historical_instruction_and_question_exports_keep_owner_identity():
    from sico.codex import backend, backend_instructions
    from sico.service import audit, audit_questions, audit_instructions
    assert backend.INSTRUCTIONS is backend_instructions.INSTRUCTIONS
    assert audit.INSTRUCTIONS is audit_instructions.INSTRUCTIONS
    for name in ('NEW_WINDOW_OPTION', 'QUESTION', 'bounded', 'questions', 'same_host_questions'):
        assert getattr(audit, name) is getattr(audit_questions, name)


def test_shell_may_not_stand_in_for_openaccess_evidence():
    """常驻指令必须写明：所有 OA 数据走平台工具 API 读写；文本检索仍可用 shell。"""
    from sico.codex import backend

    text = backend.INSTRUCTIONS
    assert "所有 OpenAccess 数据都必须通过平台工具（CDNS-IC / EMPY-AE / SNPS-CC）的 API 读写" in text
    assert "不得用 shell、文件读写或落盘镜像/预览产物访问 OA 数据库" in text
    assert "binding_refs" in text and "bind_pdk_device" in text
    assert 'get_pdk_device(sections=["geometry"])' in text
    assert "grep/find 等文本检索只用于日志、文档与输出文本" in text
