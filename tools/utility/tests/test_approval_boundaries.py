"""Approval projection executes without a service/storage implementation."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check_rule, load_manifest

ROOT = Path(__file__).resolve().parents[3]
RULES = {row['id']: row for row in load_manifest()['rules']}


@pytest.mark.parametrize('name', ['audit-question-values-runtime-free', 'codex-approval-no-service'])
def test_sources_obey_boundary(name):
    assert not check_rule(RULES[name], root=ROOT)


@pytest.mark.parametrize('module', ['codex', 'service', 'storage', 'transport', 'providers'])
def test_question_values_reject_deferred_application_import(module):
    assert violations('def run():\n    import sico.' + module,
                      RULES['audit-question-values-runtime-free'], module='sico.core.audit_questions')


@pytest.mark.parametrize('source', ['approval_requests', 'input_records'])
def test_approval_cannot_reimport_service_inside_handler(source):
    assert violations('def run():\n    from ..service.audit_records import native_record',
                      RULES['codex-approval-no-service'], module='sico.codex.' + source)


def run(script, tmp_path):
    roots = [str(ROOT / p) for p in ('tools/sico/python', 'tools/ai/python', 'tools/common/python')]
    result = subprocess.run([sys.executable, '-B', '-s', '-c', script], cwd=tmp_path,
                            env=dict(os.environ, PYTHONPATH=os.pathsep.join(roots)),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


BLOCK = r'''
import importlib.abc, os, socket, subprocess, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + '.') for p in BLOCKED):
            raise ImportError(fullname)
sys.meta_path.insert(0, Block())
def forbidden(*a, **kw):
    raise AssertionError('No process or network startup in value/projection boundary')
subprocess.Popen = socket.socket = os.system = forbidden
os.environ.clear()
'''


def test_questions_operate_without_application_runtime(tmp_path):
    run("BLOCKED = ('sico.service', 'sico.codex', 'sico.storage', 'sico.transport', "
        "'sico.providers', 'sico_ui', 'PyQt5', 'PySide', 'requests', 'httpx')\n" + BLOCK + r'''
from sico.core.audit_questions import bounded, questions, same_host_questions
source = [{'id':'scope','header':'范围','question':'请选择','options':[]}]
value = questions(source)
assert value == source and value is not source and value[0] is not source[0]
assert same_host_questions(source, value)
try:
    bounded('secret\x00', 100)
except ValueError:
    pass
else:
    raise AssertionError('control character admitted')
assert not any(n == p or n.startswith(p + '.') for n in sys.modules for p in BLOCKED)
''', tmp_path)


@pytest.mark.parametrize('kind', ['commandExecution', 'fileChange', 'permissions'])
def test_real_approval_and_mailbox_without_service(tmp_path, kind):
    run("KIND = " + repr(kind) + "\nBLOCKED = ('sico.service', 'sico.storage', "
        "'sico.providers', 'sico_ui', 'PyQt5', 'PySide', 'requests', 'httpx')\n" + BLOCK + r'''
from copy import deepcopy
from threading import RLock, Thread
from types import SimpleNamespace
from sico.core.input_scope import InputScope
from sico.codex.approval_requests import open_request, response
from sico.codex.input_requests import InputRequests
from sico.orchestration.codex import ChildIdentity, ChildTaskPolicy, CodexChildTask
lock, sent, events, rows = RLock(), [], [], {}
task = {'id':'task'}
def send(frame, **kw):
    acquired = []
    def check_lock():
        with lock:
            acquired.append(True)
    thread = Thread(target=check_lock)
    thread.start(); thread.join(2)
    assert acquired and not thread.is_alive(), 'wire response sent while locked'
    sent.append(frame)
rpc = SimpleNamespace(send=send)
owner = InputRequests(lock=lock, task=lambda:task, rpc=lambda:rpc, thread=lambda:'parent',
    child_current=lambda *a, **kw:None, emit=lambda *a:events.append(a), withdraw=lambda *a, **kw:[])
child = CodexChildTask(ChildIdentity('child','parent'), ChildTaskPolicy('检查','inspect'))
scope = InputScope('task', {'target':'dut'}, 'parent','parent-turn','chat',{},True,True)
origin = {'thread_id':'child','turn_id':'child-turn','task_id':'task'}
changes = [{'path':'/tmp/output','kind':{'type':'add'},'diff':'+hello'}]
runtime = SimpleNamespace(lock=lock, snapshot=lambda:scope,
    child_scope=lambda value:origin, approval_changes=lambda key:deepcopy(changes), activity=lambda *a:None)
class Surface:
    runtime = runtime
    requests = owner
    index = SimpleNamespace(audits=rows, data={'source':{'digest':'digest'}})
    @property
    def pending(self):
        return owner.snapshot()
    def active_work(self):
        return {'work_id':'work','stage_id':'stage','source':'source'}
    def emit(self, kind, row):
        events.append((kind, deepcopy(row)))
        if kind == 'prepared':
            rows[row['id']] = deepcopy(row)
    def open_input(self, key, pending, payload, *, claim):
        owner.open(key, pending, lambda:self.emit('opened', payload), claim=claim)
audit = Surface()
params = {'threadId':'child','turnId':'child-turn','itemId':'operation',
    'command':'printf hello','permissions':{'fileSystem':{'write':['/tmp/output']}}}
event = {'id':7,'method':'item/'+KIND+'/requestApproval','params':params}
open_request(audit, event, 'parent-turn', child=child)
open_request(audit, event, 'parent-turn', child=child)
assert len(rows) == len(owner.snapshot()) == 1 and not sent
row = next(iter(rows.values()))
assert row['child_thread_id']=='child' and row['parent_thread_id']=='parent'
assert row['child_turn_id']=='child-turn' and row['input_origin']==origin
assert row['evidence']==[{'id':'source','digest':'digest'}] and row['context']==scope.context
pending = owner.snapshot()[row['id']]
params['permissions']['fileSystem']['write'].append('/not-approved')
row['reply'] = {'answers':{'codex_approval':{'choice':
    '批准本回合权限' if KIND == 'permissions' else '仅批准本次', 'text':''}}}
result = response(row, pending)
assert result == ({'permissions':{'fileSystem':{'write':['/tmp/output']}}, 'scope':'turn'}
                  if KIND=='permissions' else {'decision':'accept'})
owner.resolve(row['id'], lambda:None)
open_request(audit, {'id':7,'method':event['method'],'params':{**params,
    'permissions':{'fileSystem':{'write':['/tmp/output']}}}}, 'parent-turn', child=child)
assert not owner.snapshot() and 'waiting_audits' not in task
scope = InputScope('task', {}, 'parent','parent-turn','chat',{'access':{'approval':'never'}},True,True)
event = {**event, 'id':8}
open_request(audit, event, 'parent-turn', child=child)
open_request(audit, event, 'parent-turn', child=child)
assert len(sent)==1 and sent[0]['id']==8 and len(rows)==1
assert sent[0]['result'] == ({'permissions':{'fileSystem':{'entries':[]},'network':{'enabled':False}},
                            'scope':'turn'} if KIND=='permissions' else {'decision':'decline'})
assert not any(n == p or n.startswith(p + '.') for n in sys.modules for p in BLOCKED)
''', tmp_path)
