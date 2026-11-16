"""Project dependency evidence loads and resolves without transport or services."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check_rule, load_manifest

ROOT = Path(__file__).resolve().parents[3]
RULE = next(row for row in load_manifest()['rules']
            if row['id'] == 'core-read-dependencies-one-way-boundary')


def test_sources_obey_boundary():
    assert not check_rule(RULE, root=ROOT)


@pytest.mark.parametrize('source', [
    'from ..transport.framing import strict_json',
    'def parse():\n    from sico.transport.framing import strict_json',
    'import sico.service.workbench as workbench',
    'def query():\n    import sico.codex.wait_reads',
    'from sico.storage.journal import SessionJournal',
    'import sico.providers',
])
def test_dependency_contract_rejects_upward_imports(source):
    assert violations(source, RULE, module='sico.core.read_dependencies')


def test_real_document_registry_works_without_application_runtimes(tmp_path):
    script = r'''
import importlib.abc
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

BLOCKED = ('sico.transport', 'sico.service', 'sico.storage', 'sico.providers', 'sico.codex',
           'sico_ui', 'PyQt5', 'PySide', 'requests', 'httpx')
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + '.') for prefix in BLOCKED):
            raise ImportError('forbidden runtime dependency: ' + fullname)
sys.meta_path.insert(0, Block())
def forbidden(*args, **kwargs):
    raise AssertionError('Dependency evidence must not start processes or connect to a service')
subprocess.Popen = os.system = os.fork = socket.socket = forbidden
os.environ.clear()
from cadai.json_values import strict_json
from sicostate import project_root
from sico.core.contracts import BoundContext, ToolCall
from sico.core import read_dependencies
from sico.core.read_dependencies import MANIFEST, ProjectReadDependencies
from sico.core.tools import ToolRegistry
from sico.adapters.documents import register_documents
assert read_dependencies.strict_json is strict_json
root = Path.cwd()
file = root / 'notes.txt'
file.write_text('local evidence')
context = BoundContext('instance', 'generation', 'target', {'cwd': str(root)})
assert ProjectReadDependencies(root)({'path': 'notes.txt'}, context) is None
assert not (root / '.sico').exists()
manifest = project_root(root, create=True) / MANIFEST
manifest.parent.mkdir(parents=True)
raw = b'{"version":1,"files":[{"path":"notes.txt","depends_on":[]}]}'
manifest.write_bytes(raw)
registry = ToolRegistry()
register_documents(registry, root)
reader = registry.resolve('read_project_document')
assert reader.input_dependencies({'path':'notes.txt'}, context) == frozenset()
assert registry.resolve('search_project_documents').input_dependencies is None
assert registry.execute(ToolCall('first', 'read_project_document', {'path':'notes.txt'}), context).data['text'] == 'local evidence'
assert manifest.read_bytes() == raw
# Proof is frozen at registry construction and invalidated by any byte change.
manifest.write_bytes(raw + b'\n')
assert reader.input_dependencies({'path':'notes.txt'}, context) is None
# This proof controls pending-decision admission; ordinary authorized reads remain usable.
assert registry.execute(ToolCall('second', 'read_project_document', {'path':'notes.txt'}), context).data['text'] == 'local evidence'
manifest.write_bytes(b'{"version":1,"version":1,"files":[]}')
assert ProjectReadDependencies(root)({'path':'notes.txt'}, context) is None
assert read_dependencies.reference_dependencies({}, context) == frozenset()
assert read_dependencies.target_dependencies({}, context) == frozenset({'target'})
assert read_dependencies.manual_dependencies({'install_root':'/not-used'}, context) is None
assert read_dependencies.manual_dependencies({}, context) == frozenset()
assert not any(name == prefix or name.startswith(prefix + '.')
               for name in sys.modules for prefix in BLOCKED)
'''
    roots = [str(ROOT / name) for name in
             ('tools/sico/python', 'tools/ai/python', 'tools/common/python')]
    result = subprocess.run([sys.executable, '-B', '-S', '-c', script], cwd=tmp_path,
                            env=dict(os.environ, PYTHONPATH=os.pathsep.join(roots)),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
