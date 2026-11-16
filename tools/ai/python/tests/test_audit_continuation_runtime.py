"""Pinned app-server question/turn boundaries through an isolated Responses gateway."""

import threading
import time

import pytest
from test_agent_codex_native import answer, gateway, native_codex, settings  # noqa: F401
from test_codex_child_input_runtime import call_named
from sico.codex.backend import CodexBackend
from sico.codex.rpc import AppServer
from sico.core.contracts import BoundContext, ToolResult
from sico.core.tools import ToolRegistry, ReadTool, no_arguments
from sico.service.workbench import attach_workbench
from sico.storage.journal import SessionJournal


@pytest.mark.parametrize('interrupt', [False, True])
def test_native_question_answer_uses_live_or_fresh_turn(tmp_path, gateway, native_codex,
                                                      monkeypatch, interrupt):
    questions = [{'id': 'scope', 'header': 'Scope', 'question': 'Only inspect?',
                  'options': [{'label': 'Inspect', 'description': 'Read only'}]}]
    gateway.responses.put(lambda req: call_named(req, 'request_user_input', 'question', {
        'questions': questions}))
    gateway.responses.put(answer('Continued after the exact answer.'))
    registry = ToolRegistry()
    registry.register(ReadTool('get_context', 'Read synthetic target', {
        'type': 'object', 'properties': {}, 'additionalProperties': False},
        no_arguments, lambda args, target: ToolResult('ok', 'valid', {'valid': True})))
    context = BoundContext('instance', 'generation', 'target', {'cwd': str(tmp_path), 'valid': True})
    calls, notices, sent = [], [], []
    original_send = AppServer.send
    def send(self, message, **kwargs):
        sent.append(message)
        return original_send(self, message, **kwargs)
    monkeypatch.setattr(AppServer, "send", send)
    original_request, original_next = AppServer.request, AppServer.next
    def request(self, method, params, **kwargs):
        calls.append((method, params))
        return original_request(self, method, params, **kwargs)
    def next_event(self, *args, **kwargs):
        event = original_next(self, *args, **kwargs)
        notices.append(event)
        return event
    monkeypatch.setattr(AppServer, 'request', request)
    monkeypatch.setattr(AppServer, 'next', next_event)
    with SessionJournal(tmp_path, 'native-boundary') as journal:
        backend = CodexBackend(settings(gateway, native_codex, 'native'), registry, journal, context)
        backend.workbench = attach_workbench(backend)
        accept = backend._input_request
        def input_request(event, turn_id, **kwargs):
            result = accept(event, turn_id, **kwargs)
            if interrupt and event.get('method') == 'item/tool/requestUserInput' and backend.audit.pending:
                backend.runtime.rpc.request('turn/interrupt', {
                    'threadId': backend.thread_id, 'turnId': turn_id})
            return result
        monkeypatch.setattr(backend, '_input_request', input_request)
        events = []
        cancelled = threading.Event()
        worker = threading.Thread(target=lambda: events.extend(backend.run('Ask before inspecting.', cancelled.is_set)))
        worker.start()
        try:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                with backend.lock:
                    pending = backend.audit.pending
                    ready = bool(pending) and (not interrupt or all(p['rpc_id'] is None for p in pending.values()))
                if ready or not worker.is_alive():
                    break
                time.sleep(0.02)
            assert ready, {'task': backend.state.task, 'notices': notices, 'audits': backend.audit.index.audits}
            row = next(iter(backend.audit.index.audits.values()))
            assert row["work_id"] == "" and row["stage_id"] == ""
            assert any(event["kind"] == "workbench.audit.prepared"
                       for event in journal.events())
            backend.audit.answer(row['id'], {'scope': {'choice': 'Inspect', 'text': ''}},
                                 'reply', 'user', row['task_id'])
            worker.join(12)
            assert not worker.is_alive()
            assert events[-1]['kind'] == 'task.completed', backend.state.task
            assert row['status'] == 'answered'
            assert len([m for m, p in calls if m == 'turn/start']) == (2 if interrupt else 1)
            assert 'Inspect' in str(gateway.requests[-1]['input'])
            answers = [message for message in sent if 'answers' in message.get('result', {})]
            assert len(answers) == (0 if interrupt else 1)
            if interrupt:
                assert any(event['kind'] == 'workbench.audit.bound'
                           and event['payload'].get('continuation') for event in journal.events())
            from sico.core.session_events import EventCursor
            from sico.service.controller import SessionController
            controller = SessionController(backend)
            EventCursor(controller.session_id, controller.runtime_id, context).consume(
                controller.updates.read_updates())
        finally:
            cancelled.set()
            worker.join(3)
            backend.close()
            worker.join(3)
            assert not worker.is_alive()
