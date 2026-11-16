"""Native empty responses remain recoverable without replaying completed tools."""

import pytest

from test_agent_codex_native import (
    CONTEXT, answer, gateway as gateway, native_codex as native_codex, registry, settings, sse,
)
from sico.codex.backend import CodexBackend
from sico.codex.turn_response import TurnResponse
from sico.storage.journal import SessionJournal


@pytest.mark.parametrize('body', [sse(), sse({
    'type': 'reasoning', 'id': 'empty-reasoning', 'summary': [],
    'content': [{'type': 'reasoning_text', 'text': '"'}],
})], ids=['empty', 'reasoning-only'])
def test_native_empty_response_after_tool_does_not_complete_or_replay(
        tmp_path, gateway, native_codex, body):
    gateway.responses.put(sse(
        {'type': 'message', 'id': 'commentary', 'role': 'assistant',
         'content': [{'type': 'output_text', 'text': '准备检查。'}]},
        {'type': 'function_call', 'id': 'read', 'call_id': 'read-call',
         'name': 'mcp__sico_ic__get_context', 'arguments': '{}'},
    ))
    gateway.responses.put(body)
    seen = []
    with SessionJournal(tmp_path, 'empty-response') as journal:
        backend = CodexBackend(settings(gateway, native_codex, 'flat'), registry(seen), journal, CONTEXT)
        try:
            events = list(backend.run('检查当前上下文'))
            assert events[-1]['kind'] == 'task.needs_reconcile', events[-4:]
            assert '没有提供有效答复' in events[-1]['payload']['message']
            assert len(gateway.requests) == 2
            assert len([e for e in events if e['kind'] == 'tool.finished'
                        and e['payload']['name'] == 'get_context']) == 2  # captured facts + model call
            gateway.responses.put(answer('已核对现有回执，可以继续。'))
            backend.acknowledge_interrupted()
            continued = list(backend.run('继续，使用已有回执'))
            assert continued[-1]['kind'] == 'task.completed'
            assert len(gateway.requests) == 3
        finally:
            backend.close()


def test_later_empty_step_cannot_reuse_earlier_commentary():
    response = TurnResponse()
    response.observe('item/completed', {'item': {'type': 'agentMessage', 'text': '处理中'}})
    response.observe('item/completed', {'item': {'type': 'mcpToolCall'}})
    response.observe('item/completed', {'item': {'type': 'reasoning'}})
    assert not response.usable
    response.observe('item/completed', {'item': {'type': 'agentMessage', 'text': '  '}})
    assert not response.usable
    response.observe('item/completed', {'item': {'type': 'agentMessage', 'text': '已完成核对'}})
    assert response.usable


def test_long_wait_status_marks_missing_answer_without_exposing_reasoning():
    from datetime import datetime, timedelta, timezone
    from sico_ui.tasks import session_status

    state = dict(fault='', closing=False, cancelling=False, busy=True, pending=0,
                 task={'status': 'executing', 'activity': {
                     'phase': 'waiting_model', 'label': '等待 LLM 响应', 'turn': 1,
                     'started_at': (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()}})
    assert '尚未收到可用答复' in session_status(state)
    state['task']['activity']['phase'] = 'tool_call'
    assert '尚未收到可用答复' not in session_status(state)


@pytest.mark.parametrize("compact", [False, True])
def test_compaction_completion_is_usable_only_for_explicit_operation(compact):
    response = TurnResponse(compact=compact)
    response.observe("item/started", {"item": {"type": "contextCompaction"}})
    assert not response.usable
    response.observe("item/completed", {"item": {"type": "contextCompaction"}})
    assert response.usable is compact
    response.observe("item/started", {"item": {"type": "reasoning"}})
    assert not response.usable
