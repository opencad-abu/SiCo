"""Exercise identity, event and result values without application/runtime access."""

import importlib.abc
import os
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(sys.argv[1])
sys.path[:0] = [str(ROOT / value) for value in (
    'tools/sico/python', 'tools/ai/python', 'tools/common/python')]
BLOCKED = ('sico.service', 'sico.storage', 'sico.transport', 'sico.providers', 'sico.codex',
           'sico_ui', 'PyQt5', 'PySide', 'requests', 'httpx')


class BlockRuntime(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (any(fullname == prefix or fullname.startswith(prefix + '.') for prefix in BLOCKED)
                or fullname.startswith('cadai.')
                and fullname not in {'cadai.json_values', 'cadai.result_contract'}):
            raise ImportError('blocked runtime import: ' + fullname)
        return None


def forbidden(*args, **kwargs):
    raise AssertionError('Value contract attempted external I/O')


sys.meta_path.insert(0, BlockRuntime())
subprocess.Popen = os.system = socket.socket.connect = socket.socket.connect_ex = forbidden
os.environ.clear()

from sico.core.contracts import BoundContext, ToolCall, ToolResult, TASK_CONTRACT
from sico.core.host_contracts import host_identity, session_address_record
from sico.core.input_scope import InputScope
from sico.core.session_events import EventCursor, SESSION_CONTRACT
from sico.core.native_origin import NativeOrigins
from sico.core.display_batch import DisplayBatch, DISPLAY_CONTRACT
from cadai.result_contract import SCHEMA, digest, evaluate, normalize_capture

context = BoundContext('instance', 'generation', 'target', {'path': ['original']})
record = context.record()
record['snapshot']['path'].append('foreign')
assert context.snapshot == {'path': ['original']}
assert BoundContext.from_record(context.record()) == context
assert host_identity(dict(instance_id='i', generation='g', bridge_id='b', router_id='r'))
address = dict(project_id='p', service_id='s', session_id='q', runtime_id=None)
assert session_address_record(address, session_id='q') == address
try:
    session_address_record(address, session_id='foreign')
except ValueError:
    pass
else:
    raise AssertionError('Foreign address accepted')
scope = InputScope('task', context.record(), 'thread', 'turn', 'chat', {}, True, True)
assert scope.context == context.record()
call = ToolCall('call', 'read', {'nested': [False, 0, None]})
copy = call.record()
copy['input']['nested'].clear()
assert call.inputs['nested'] == [False, 0, None]
assert ToolResult(data={'values': [False, 0, None]}).record() == {
    'status': 'ok', 'summary': '', 'data': {'values': [False, 0, None]}, 'untrusted': True}
cursor = EventCursor('session', 'runtime', context)
assert isinstance(cursor.native_origins, NativeOrigins)
started = dict(contract=TASK_CONTRACT, session_id='session', sequence=1, task_id='task',
               kind='task.started', payload=dict(context=context.record(), text='read'))
batch = dict(contract=SESSION_CONTRACT, session_id='session', runtime_id='runtime',
             instance_id='instance', generation='generation', events=[started])
assert cursor.consume(batch) == [started]
assert DisplayBatch(cursor.identity, 1, 1, 1, 1, ()).contract == DISPLAY_CONTRACT
try:
    cursor.consume(batch)
except ValueError:
    pass
else:
    raise AssertionError('Duplicate event accepted')
meta = dict(source='maeReadResDB', scope='point_expressions', target=['lib', 'cell', 'maestro'],
            history='history', library_path='/lib', test_points=1, outputs=1)
rows = [['coordinate', 'nominal', 0, None], ['test', 'test', 'nominal', 0, 'done', None],
        ['definition', 'test', 'value', 'point', 'expression'],
        ['output', 'test', 'nominal', 0, 'value', 'scalar', 0.0]]
result = normalize_capture(meta, rows)
assert result['schema'] == SCHEMA
assert digest(result) == digest(normalize_capture(meta, list(reversed(rows))))
contract = dict(source='explicit', expected_test_points=[dict(test='test', corner='nominal', point=0)],
                specifications=[dict(test='test', output='value', unit='V', lower=0, upper=1)])
report = evaluate(result, contract)
assert report['spec_qualified'] is True
print('identity/event/result values run without runtime imports or external I/O')
