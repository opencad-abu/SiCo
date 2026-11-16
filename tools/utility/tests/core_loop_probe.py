"""Run the real core in isolation with a memory journal and a scripted provider."""

import importlib.abc
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

ROOT, MODE = Path(sys.argv[1]), sys.argv[2]
sys.path[:0] = [str(ROOT / value) for value in (
    'tools/sico/python', 'tools/ai/python', 'tools/common/python')]
BLOCKED = ('sico.service', 'sico.storage', 'sico.transport', 'sico.providers',
           'sico.codex', 'sico_ui', 'PyQt5', 'PySide', 'requests', 'httpx')


class BlockRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (any(fullname == prefix or fullname.startswith(prefix + '.') for prefix in BLOCKED)
                or fullname.startswith('cadai.') and fullname != 'cadai.json_values'):
            raise ImportError('blocked runtime import: ' + fullname)
        return None


def forbidden(*args, **kwargs):
    raise AssertionError('Core attempted external I/O')


sys.meta_path.insert(0, BlockRuntime())
subprocess.Popen = os.system = socket.socket.connect = socket.socket.connect_ex = forbidden
os.environ.clear()

from sico.core.contracts import BoundContext, ModelTurn, ToolCall, ToolResult, json_copy
from sico.core.loop import AgentLoop
from sico.core.tools import Tool, ToolRegistry, no_arguments
from sico.core.backend_support import PYTHON_SUPPORT, CODEX_SUPPORT, UnsupportedCapability
from sico.core.target_validation import context_method, validate_target_receipt

assert PYTHON_SUPPORT.capabilities < CODEX_SUPPORT.capabilities
assert context_method(BoundContext('i', 'g', 't', {}), []) == 'get_context'
validate_target_receipt({'status': 'ok', 'data': {'valid': True}})
try:
    PYTHON_SUPPORT.require('native_input')
except UnsupportedCapability:
    pass
else:
    raise AssertionError('Unsupported native input was accepted')


class MemoryJournal:
    state = None

    def __init__(self):
        self._events = []

    def append(self, kind, payload, state):
        self.state = state
        event = {
            'sequence': len(self._events) + 1,
            'kind': kind,
            'task_id': state.task.get('id', ''),
            'payload': json_copy(payload),
        }
        self._events.append(event)
        return event

    def events(self):
        return tuple(self._events)

    def artifact(self, content):
        raise AssertionError('Small fixture must not offload history')


closed, calls, requests, cancelled = [], [], [], []


class Provider:
    def stream(self, system, messages, schemas, cancel):
        assert system == 'explicit instructions'
        requests.append(messages)
        try:
            if len(requests) == 1:
                yield ModelTurn('', (ToolCall('one', 'read', {}),))
            else:
                assert messages[-1]['role'] == 'tool'
                yield ModelTurn('')
        finally:
            closed.append(True)


def handle(inputs, context):
    calls.append(context.record())
    if MODE == 'bad_receipt':
        return ToolResult(data=float('nan'))
    return ToolResult(data={'source': 'memory'})


context = BoundContext('i', 'g', 'w', {})
journal, registry = MemoryJournal(), ToolRegistry()
registry.register(Tool('read', 'read', {'type': 'object'}, no_arguments, handle, effect='read'))
loop = AgentLoop(Provider(), registry, journal, context, instructions=lambda: 'explicit instructions')
assert loop.state is not None
stream = loop.run('inspect', lambda: bool(cancelled))
for event in stream:
    if event['kind'] == 'tool.started' and MODE == 'cancel':
        cancelled.append(True)
    if event['kind'] == 'tool.started' and MODE == 'disconnect':
        stream.close()
        break
expected = {'ok': 'completed', 'cancel': 'cancelled',
            'disconnect': 'needs_reconcile', 'bad_receipt': 'needs_reconcile'}[MODE]
assert loop.state.task['status'] == expected
assert journal.state is loop.state
assert not loop.active
assert len(closed) == len(requests)
assert len(calls) == (1 if MODE in ('ok', 'bad_receipt') else 0)
if MODE == 'cancel':
    result = next(row['payload']['result'] for row in journal.events() if row['kind'] == 'tool.finished')
    assert result['status'] == 'cancelled_before_start'
if expected == 'needs_reconcile':
    before = len(calls)
    resumed = AgentLoop(Provider(), registry, journal, context,
                        instructions=lambda: 'explicit instructions')
    try:
        list(resumed.run('do not replay'))
    except RuntimeError:
        pass
    else:
        raise AssertionError('Unreconciled task was replayed')
    resumed.acknowledge_interrupted()
    assert resumed.state.task['status'] == 'cancelled' and len(calls) == before
assert not any(any(name == prefix or name.startswith(prefix + '.') for prefix in BLOCKED)
               for name in sys.modules)
print(json.dumps({'mode': MODE, 'status': expected, 'tools_executed': len(calls),
                  'provider_streams_closed': len(closed)}))
