"""Stopping model generation never demands reconciliation for model-only work."""

import queue
from types import SimpleNamespace

import pytest
from test_codex_elicitation import running
from wait_reads_fixture import host_question
from sico.codex.rpc import RpcError, RpcRejected


def test_model_only_stop_pauses_without_interrupt_or_reconcile(running):
    backend, _, sent = running
    host_question(backend, kind="target")
    closed = []
    backend.runtime.close = lambda: closed.append(True)
    backend.runtime.rpc.next = lambda: (_ for _ in ()).throw(queue.Empty())
    backend.runtime.rpc.request = lambda *args, **kwargs: pytest.fail(
        "model-only stop must not ask the native turn to interrupt")
    backend.cancelled = lambda: True

    events = list(backend._consume_turn("turn", backend.cancelled))

    assert events[-1]["kind"] == "task.cancelled"
    assert "模型思考已暂停" in events[-1]["payload"]["message"]
    assert closed == [True]
    assert not sent and not backend.audit.pending
    assert not backend.state.task.get("waiting_audits")


@pytest.mark.parametrize('terminal', ['completed', 'interrupted', 'foreign', 'missing'])
def test_external_work_interrupt_rejection_waits_for_exact_terminal_without_replay(
        running, monkeypatch, terminal):
    backend, _, sent = running
    host_question(backend, kind="target")
    backend.state.task["pending_calls"] = [{
        "id": "call-0", "name": "execute_circuit_operation",
    }]
    backend.runtime.close = lambda: None
    calls, clock = [], [0]
    def interrupt(method, params, **kwargs):
        calls.append((method, params))
        raise RpcRejected('Codex turn/interrupt failed (code -32600)')
    backend.runtime.rpc.request = interrupt
    frames = [] if terminal == 'missing' else [{'method': 'turn/completed', 'params': {
        'threadId': 'foreign' if terminal == 'foreign' else 'thread', 'turnId': 'turn',
        'turn': {'id': 'turn', 'status': 'completed' if terminal == 'foreign' else terminal}}}]
    def next_frame():
        if frames:
            return frames.pop(0)
        clock[0] += 16
        raise queue.Empty
    backend.runtime.rpc.next = next_frame
    import sico.codex.backend as module
    monkeypatch.setattr(module, 'time', SimpleNamespace(
        monotonic=lambda: clock[0], strftime=module.time.strftime, gmtime=module.time.gmtime))
    backend.cancelled = lambda: True
    if terminal in {'foreign', 'missing'}:
        with pytest.raises(RpcError, match='中断请求未及时结束'):
            list(backend._consume_turn('turn', backend.cancelled))
    else:
        events = list(backend._consume_turn('turn', backend.cancelled))
        assert events[-1]['kind'] == 'task.cancelled'
        finished = [e for e in events if e['kind'] == 'tool.finished']
        assert finished[-1]['payload']['result']['status'] == 'unconfirmed'
        assert not any(e['kind'] == 'task.needs_reconcile' for e in events)
    assert len(calls) == 1 and calls[0][0] == 'turn/interrupt'
    assert not sent and not backend.audit.pending
    assert not backend.state.task.get('waiting_audits')
