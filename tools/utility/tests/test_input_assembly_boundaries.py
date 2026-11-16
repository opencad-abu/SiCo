"""The Codex input composition and detached scope do not import service owners."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check_rule, load_manifest

ROOT = Path(__file__).resolve().parents[3]
RULES = {row['id']: row for row in load_manifest()['rules']}


@pytest.mark.parametrize('name', ['input-scope-runtime-free', 'input-assembly-no-service'])
def test_sources_obey_boundary(name):
    assert not check_rule(RULES[name], root=ROOT)


@pytest.mark.parametrize('module', ['service', 'codex', 'storage', 'transport', 'providers'])
def test_scope_rejects_function_local_application_import(module):
    assert violations('def run():\n    import sico.' + module,
                      RULES['input-scope-runtime-free'], module='sico.core.input_scope')


def test_backend_cannot_reimport_service_assembly_inside_constructor():
    assert violations('def init():\n    from ..service.audit_runtime import request_owner',
                      RULES['input-assembly-no-service'], module='sico.codex.backend')


def run(script, tmp_path):
    roots = [str(ROOT / p) for p in ('tools/sico/python', 'tools/ai/python', 'tools/common/python')]
    result = subprocess.run([sys.executable, '-B', '-s', '-c', script], cwd=tmp_path,
                            env=dict(os.environ, PYTHONPATH=os.pathsep.join(roots)),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_scope_imports_without_any_application_runtime(tmp_path):
    run(r'''
import importlib.abc, sys
BLOCKED = ('sico.service', 'sico.codex', 'sico.storage', 'sico.transport', 'sico.providers',
           'sico_ui', 'PyQt5', 'PySide', 'cadai', 'requests', 'httpx')
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + '.') for p in BLOCKED):
            raise ImportError(fullname)
sys.meta_path.insert(0, Block())
from sico.core.input_scope import InputScope
assert InputScope('task', {}, '', '', 'chat', {}, True, True).admitted
assert not any(n == p or n.startswith(p + '.') for n in sys.modules for p in BLOCKED)
''', tmp_path)


def test_assembled_child_lifecycle_without_service_or_process_start(tmp_path):
    run(r'''
import importlib.abc, os, socket, subprocess, sys
from threading import RLock, Thread
from types import SimpleNamespace
BLOCKED = ('sico.service', 'sico.storage', 'sico.providers', 'sico_ui', 'PyQt5', 'PySide',
           'requests', 'httpx')
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + '.') for p in BLOCKED):
            raise ImportError(fullname)
sys.meta_path.insert(0, Block())
def forbidden(*a, **kw):
    raise AssertionError('No process or network operation in input assembly')
subprocess.Popen = socket.socket = os.system = forbidden
os.environ.clear()
from sico.codex.input_assembly import request_owner, child_inputs, capture
from sico.orchestration.codex import ChildIdentity, ChildTaskPolicy, CodexChildTask
lock = RLock()
events, sent, refreshes, subagents = [], [], [], []
loop = SimpleNamespace(lock=lock, state=SimpleNamespace(
    task={'id':'task', 'status':'executing'}, context=SimpleNamespace(record=lambda: {'target':'cell'})),
    thread_id='parent', steering=SimpleNamespace(turn_id='parent-turn'), active=True,
    cancelled=lambda: False, stale=False, _event=lambda *a: events.append(a),
    orchestration=SimpleNamespace(children={}), _record_subagent=lambda item: subagents.append(item))
# Assembly precedes runtime, audit and history construction.
loop.inputs = request_owner(loop)
loop.child_inputs = child_inputs(loop)
def send(frame, **kw):
    acquired = []
    def check_lock():
        with lock:
            acquired.append(True)
    thread = Thread(target=check_lock)
    thread.start(); thread.join(2)
    assert acquired and not thread.is_alive(), 'control reply sent while state lock held'
    sent.append(frame)
loop.runtime = SimpleNamespace(rpc=SimpleNamespace(send=send))
loop.native_history = SimpleNamespace(refresh=lambda: refreshes.append(True),
    origins=SimpleNamespace(turns={('parent','parent-turn')}))
def withdraw(reason, *, child_id=None):
    replies = []
    for key, pending in loop.inputs.snapshot().items():
        if pending.get('child_thread_id') == child_id:
            replies.extend(loop.inputs.cancellation(pending))
            loop.inputs.cancel(key, lambda: None)
    return replies
loop.audit = SimpleNamespace(end_inputs=lambda *a, **kw: loop.inputs.finish(), withdraw_inputs=withdraw)
child = CodexChildTask(ChildIdentity('child','parent'), ChildTaskPolicy('child','inspect'))
loop.orchestration.children['child'] = child
loop.child_inputs.own(child)
assert child.task_id == 'task' and child.parent_turn_id == 'parent-turn'
try:
    loop.child_inputs.bind('child', 'parent-turn')
except ValueError as exc:
    assert str(exc) == 'unverified_child_turn'
else:
    raise AssertionError('parent turn admitted as a child turn')
loop.child_inputs.bind('child','child-turn')
loop.inputs.open('request', {'rpc_id':7, 'child_thread_id':'child', 'binding': {
    'connection_id':loop.inputs.connection_id, 'threadId':'child'}}, lambda: None)
assert loop.child_inputs.notification({'method':'turn/completed','params': {
    'threadId':'child', 'turn':{'id':'child-turn','status':'completed'}}})
assert child.input_closed and not child.turn_id and child.finished
assert refreshes and len(sent) == 1 and sent[0]['id'] == 7 and sent[0]['error']['code'] == -32800
assert not loop.inputs.snapshot() and 'waiting_audits' not in loop.state.task
loop.child_inputs.notification({'method':'thread/closed','params':{'threadId':'child'}})
assert len(sent) == 1
assert not any(n == p or n.startswith(p + '.') for n in sys.modules for p in BLOCKED)
''', tmp_path)


def test_real_backend_constructs_without_service(tmp_path):
    # Construction now loads shared questions and Codex records without service.
    # Runtime assembly has separate start/resume boundary coverage.
    run(r'''
import importlib.abc, os, subprocess, sys
from pathlib import Path
from types import SimpleNamespace
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'sico.service' or fullname.startswith('sico.service.'):
            raise ImportError(fullname)
sys.meta_path.insert(0, Block())
def forbidden(*a, **kw):
    raise AssertionError('No process during backend construction')
subprocess.Popen = forbidden
os.environ.pop('SICO_HOME', None)
os.environ.pop('CAD_HOME', None)
from sico.codex.backend import CodexBackend
from sico.core.contracts import BoundContext
from sico.core.tools import ToolRegistry
from sico.storage.journal import SessionJournal
with SessionJournal(Path.cwd(), 'boundary') as journal:
    backend = CodexBackend(SimpleNamespace(options={}, environment={}), ToolRegistry(), journal,
        BoundContext('instance','generation','window',{'valid':True}))
    assert backend.inputs is not None and backend.child_inputs.active() == []
    backend.close()
assert not any(n == 'sico.service' or n.startswith('sico.service.') for n in sys.modules)
''', tmp_path)
