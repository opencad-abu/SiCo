"""Actual task/audit owners driven by a bounded synthetic app-server event stream."""

from collections import deque
from types import SimpleNamespace
import queue
import time

from codex_thread_fixture import bind_thread
from sico.codex.backend import CodexBackend
from sico.core.contracts import BoundContext, ToolResult
from sico.core.tools import ReadTool, ToolRegistry, no_arguments
from sico.service.workbench import attach_workbench
from sico.storage.journal import SessionJournal


def ended(status='interrupted', turn='turn'):
    return {'method': 'turn/completed', 'params': {
        'threadId': 'thread', 'turnId': turn, 'turn': {'id': turn, 'status': status}}}


class AuditTurn:
    def __init__(self, path):
        self.journal = SessionJournal(path, 'audit-turn')
        self.context = BoundContext('instance', 'generation', 'target', {
            'valid': True, 'cwd': str(path),
            'cellview': {'lib': 'SicoTest', 'cell': 'probe', 'view': 'schematic'}})
        registry = ToolRegistry()
        registry.register(ReadTool('get_context', 'Read synthetic target', {
            'type': 'object', 'properties': {}, 'additionalProperties': False},
            no_arguments, lambda args, target: ToolResult('ok', 'valid', {
                'valid': True, 'source_valid': True})))
        self.backend = CodexBackend(SimpleNamespace(idle_timeout=1, tool_timeout=60),
                                    registry, self.journal, self.context)
        self.backend.workbench = attach_workbench(self.backend)
        self.backend.runtime = SimpleNamespace(rpc=self, close=lambda: None)
        bind_thread(self.backend)
        self.now = 0
        self.backend.task_journal.clock = lambda: SimpleNamespace(
            monotonic=lambda: self.now, strftime=time.strftime, gmtime=time.gmtime)
        self.frames, self.sent, self.calls, self.rows = deque(), [], [], []
        self.on_start, self.on_wait, self.on_continue = lambda: None, lambda: None, lambda: None
        self.cancel = False
        self.status = 'interrupted'
        self.polls = 0
        self.phase = 'initial'
        self.opened = False

    def question(self, key='scope', *, host=False):
        backend = self.backend
        evidence = next(iter(backend.workbench.index.data))
        prepared = backend.audit.prepare({
            'request_id': key, 'title': key, 'recommendation': '', 'rationale': 'Test',
            'evidence_ids': [evidence], 'review_class': 'blocking',
            'questions': [{'id': key, 'header': key, 'question': 'Only inspect?',
                'options': [{'label': 'Inspect', 'description': 'Read only'}]}]}, self.context).data
        row = backend.audit.index.audits[prepared['audit_id']]
        if host:
            backend.audit.open_host(row, 'test')
        else:
            backend.audit.open({'id': 41 + len(self.rows), 'params': {
                'threadId': 'thread', 'turnId': 'turn', 'itemId': key,
                'isBlocking': True, 'questions': prepared['questions']}})
        self.rows.append(row)
        return row

    def answer(self, row=None):
        row = row or self.rows[0]
        return self.backend.audit.answer(row['id'], {
            q['id']: {'choice': 'Inspect', 'text': ''} for q in row['questions']},
            'reply_' + row['id'], 'user', row['task_id'])

    def request(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method != 'turn/start':
            return {}
        if self.phase == 'initial':
            self.phase = 'running'
            self.backend.workbench.stage({'work_title': 'Probe', 'stage_title': 'Input',
                'objective': 'Answer after turn termination', 'acceptance': 'Continue'}, self.context)
            self.frames.append(ended(self.status))
            return {'turn': {'id': 'turn'}}
        self.phase = 'continuing'
        self.on_continue()
        self.frames.extend([
            {'method': 'item/completed', 'params': {
                'threadId': 'thread', 'turnId': 'continued',
                'item': {'id': 'message', 'type': 'agentMessage', 'text': '已继续完成。'}}},
            ended('completed', 'continued')])
        return {'turn': {'id': 'continued'}}

    def next(self):
        if self.phase == 'running' and not self.opened:
            self.opened = True
            self.on_start()
        if self.frames:
            frame = self.frames.popleft()
            if frame['method'] == 'turn/completed' and self.phase == 'running':
                self.phase = 'waiting'
            return frame
        self.now += 2
        raise queue.Empty()

    def poll_notifications(self):
        if self.phase == 'waiting':
            self.polls += 1
            assert self.polls < 8, 'audit wait failed to make progress'
            self.on_wait()

    def send(self, message, **kwargs):
        self.sent.append(message)

    def run(self):
        return list(self.backend.run('Ask', lambda: self.cancel, context=self.context,
                                     input_id='probe', turn_options={}))

    def starts(self):
        return [params for method, params in self.calls if method == 'turn/start']

    def close(self):
        self.backend.close()
        self.journal.close()
