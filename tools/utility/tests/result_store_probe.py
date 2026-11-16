"""Offline WorkbenchIndex.material with all application tools and writes denied."""

import hashlib
import importlib.abc
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(sys.argv[1])
sys.path[:0] = [str(ROOT / path) for path in (
    'tools/sico/python', 'tools/ai/python', 'tools/common/python')]
BLOCKED = ('cadai.result_tools', 'cadai.skill_result', 'cadai.circuit_schema',
           'sico.service', 'sico.codex', 'sico.transport', 'sico.providers',
           'sico_ui', 'PyQt5', 'PySide', 'httpx', 'requests')


class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in BLOCKED):
            raise ImportError('blocked application runtime: ' + fullname)


def forbidden(*args, **kwargs):
    raise AssertionError('Unexpected process/network operation')


sys.meta_path.insert(0, Block())
subprocess.Popen = os.system = os.fork = forbidden
socket.socket.connect = socket.socket.connect_ex = socket.socket.bind = forbidden
os.environ.clear()
from sico.storage.workbench import WorkbenchIndex, content_digest
from cadai.result_store import ARTIFACT_SCHEMAS

cases = []
for index, (prefix, schema) in enumerate(ARTIFACT_SCHEMAS.items()):
    # Both pre-activation legacy and current state roots remain directly readable.
    for state_name in ('.cad', '.sico'):
        state = Path.cwd() / str(index) / state_name
        directory = state / 'ai/measurement-results'
        directory.mkdir(parents=True)
        value = {'schema': schema, 'rows': [{'value': index}]}
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode()
        digest = hashlib.sha256(raw).hexdigest()
        ref = prefix + '_' + digest
        path = directory / (ref + '.json')
        path.write_bytes(raw)
        evidence = {'data': {'result_ref': ref, 'artifact': {
            'path': str(path), 'sha256': digest, 'bytes': len(raw)}}}
        workbench = WorkbenchIndex(SimpleNamespace(root=state / 'ai/agent', session_id='session'))
        workbench.data['saved'] = {'value': evidence, 'data_type': 'tool',
            'digest': content_digest({'value': evidence, 'inputs': {}})}
        cases.append((workbench, value))


def deny_writes(event, args):
    if event == 'open':
        path, mode, flags = args
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError('Archive read attempted write: ' + str(path))
    if event in {'os.mkdir', 'os.remove', 'os.rename', 'os.rmdir', 'os.link', 'os.symlink'}:
        raise AssertionError('Archive read attempted filesystem mutation: ' + event)


sys.addaudithook(deny_writes)
for index, value in cases:
    assert index.material('saved') == value
assert not any(name == prefix or name.startswith(prefix + '.')
               for name in sys.modules for prefix in BLOCKED)
print('Read 18 current/legacy artifacts without application runtime or writes')
