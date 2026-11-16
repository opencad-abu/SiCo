"""Storage consumes value contracts without importing transport implementations."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check_rule, load_manifest

ROOT = Path(__file__).resolve().parents[3]
RULES = {row["id"]: row for row in load_manifest()["rules"]}


@pytest.mark.parametrize("name", ["storage-no-transport-runtime", "shared-json-values-runtime-free"])
def test_sources_obey_boundaries(name):
    assert not check_rule(RULES[name], root=ROOT)


@pytest.mark.parametrize("source", [
    "from ..transport.framing import strict_json",
    "def read():\n    from sico.transport.framing import ProtocolError",
    "import sico.transport.framing as framing",
])
def test_storage_cannot_reintroduce_direct_or_deferred_transport_imports(source):
    assert violations(source, RULES["storage-no-transport-runtime"],
                      module="sico.storage.journal_records")


@pytest.mark.parametrize("module", ["sico", "cadai.responses_wrapper", "socket", "subprocess"])
def test_json_values_cannot_acquire_application_or_io_dependencies(module):
    assert violations("def decode():\n    import " + module,
                      RULES["shared-json-values-runtime-free"], module="cadai.json_values")


def test_storage_reads_and_writes_without_transport_or_application_runtime(tmp_path):
    roots = [str(ROOT / name) for name in
             ("tools/sico/python", "tools/ai/python", "tools/common/python")]
    script = r'''
import importlib.abc
import json
import os
from pathlib import Path
import pkgutil
import socket
import subprocess
import sys
from types import SimpleNamespace

BLOCKED = ('sico.transport', 'sico.service', 'sico.providers', 'sico.codex',
           'sico_ui', 'PyQt5', 'PySide', 'httpx', 'requests')
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in BLOCKED):
            raise ImportError('forbidden runtime dependency: ' + fullname)
sys.meta_path.insert(0, Block())
def forbidden(*args, **kwargs):
    raise AssertionError('storage must not create a process or network connection')
subprocess.Popen = os.system = os.fork = socket.socket = forbidden
os.environ.clear()
from cadai.json_values import ProtocolError
from sico.core.contracts import BoundContext, RunState
from sico import storage
for module in pkgutil.iter_modules(storage.__path__):
    importlib.import_module('sico.storage.' + module.name)
from sico.storage.journal import SessionJournal
from sico.storage.history import CatalogIndex, SessionReader
from sico.storage.inbox_records import read_input
from sico.storage.project_files import registry_directory, write_record
from sico.storage.host_routes import HostRoutes
from sico.storage.session_snapshot import capture, save, read
from sico.storage.session_release import save_release, released
from sico.storage.record_integrity import require_components
from sico.storage import history_policy
from sico.storage.workbench import WorkbenchIndex, content_digest

project = Path.cwd()
context = BoundContext('i', 'g', 't', {})
with SessionJournal(project, 'isolated') as journal:
    journal.append('session.created', {'context': context.record()}, RunState(context))
    directory, root = journal.directory, journal.root
    original = (directory / 'events.jsonl').read_bytes()
    snapshot = capture('isolated', context, None, {}, {}, 'project')
    save(directory, snapshot)
    assert read(directory) == snapshot
    record = dict(id='input', status='queued', origin='chat', accepted_at=1.0,
                  message=dict(kind='submit', id='input', text='kept', context=context.record()))
    write_record(directory / 'inbox/input.json', record)
    assert read_input(directory / 'inbox', 'input', confirm=True) == record
    registry_directory(project, create=True)
    routes = HostRoutes(project, 'project')
    host = dict(instance_id='i', generation='g', bridge_id='b', router_id='r')
    route = dict(contract='sico_host_route.v1', project_id='project', host=host,
                 input_id='input', service_id='service', session_id='isolated',
                 payload_digest='c' * 64, address=None)
    routes.write(host, 'input', route)
    assert routes.read(host, 'input') == route
    address = dict(project_id='project', service_id='service', session_id='isolated', runtime_id='runtime')
    save_release(directory, SimpleNamespace(session=SimpleNamespace(runtime_id='runtime'),
                                           record=lambda: address), 'operation', 1)
    event = dict(kind='session.end_observed', sequence=1,
                 payload=dict(address=address, operation_id='operation', state='drained'))
    assert released(directory, event)
    require_components(root, 'isolated', managed=True)
    assert history_policy.read(root) is None
    artifact = journal.artifact(b'{"ok":true}')
    value = dict(artifact=artifact, truncated=True)
    workbench = WorkbenchIndex(journal)
    workbench.data['evidence'] = dict(value=value, data_type='archive',
        digest=content_digest(dict(value=value, inputs={})))
    assert workbench.evidence_value('evidence') == {'ok': True}
    with (directory / 'events.jsonl').open('ab') as stream:
        stream.write(b'{"unfinished":')
    before = (directory / 'events.jsonl').read_bytes()
    assert len(SessionReader(root, 'isolated').events()) == 1
    assert CatalogIndex(root).scan()['isolated']['id'] == 'isolated'
    assert (directory / 'events.jsonl').read_bytes() == before
with SessionJournal(project, 'isolated') as recovered:
    assert (directory / 'events.jsonl').read_bytes() == original
    assert len(recovered.events()) == 1
assert not any(name == prefix or name.startswith(prefix + '.')
               for name in sys.modules for prefix in BLOCKED)
'''
    result = subprocess.run([sys.executable, "-B", "-S", "-c", script], cwd=tmp_path,
                            env=dict(os.environ, PYTHONPATH=os.pathsep.join(roots)),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
