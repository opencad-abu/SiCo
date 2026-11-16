"""EQ-08 bridge environment and archive ownership do not import service runtimes."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check_rule, load_manifest

ROOT = Path(__file__).resolve().parents[3]
RULES = {row['id']: row for row in load_manifest()['rules']}
BOUNDARIES = (
    'transport-platform-environment-runtime-free',
    'process-identity-read-only-boundary',
    'transport-service-one-way-boundary',
)


@pytest.mark.parametrize('name', BOUNDARIES)
def test_sources_obey_boundary(name):
    assert not check_rule(RULES[name], root=ROOT)


@pytest.mark.parametrize('source', [
    'from ..service.background_environment import capture_environment',
    'def inspect():\n    from ..service.background_watchdog import boot_id',
    'import sico.service.background_environment as environment',
])
def test_service_imports_are_rejected_including_deferred_imports(source):
    assert violations(source, RULES['transport-service-one-way-boundary'],
                      module='sico.transport.instance_bridge')


def run_isolated(tmp_path, body):
    roots = [str(ROOT / path) for path in
             ('tools/sico/python', 'tools/ai/python', 'tools/common/python')]
    script = 'import sys\nsys.path[:0] = ' + repr(roots) + '\n' + body
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(roots), SICO_HOME=str(ROOT))
    env.pop('CAD_HOME', None)
    result = subprocess.run([sys.executable, '-B', '-S', '-c', script], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_process_identity_imports_without_application_or_process_control(tmp_path):
    run_isolated(tmp_path, r'''
import importlib.abc
import os
import sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('subprocess', 'signal', 'sicoprocess') or any(
                fullname == p or fullname.startswith(p + '.') for p in
                ('sico.service', 'sico.transport', 'sico.storage', 'sico.codex',
                 'sico.providers', 'sico_ui', 'PyQt5', 'PySide')):
            raise ImportError('forbidden dependency: ' + fullname)
sys.meta_path.insert(0, Block())
from sico.process_identity import boot_id, process_identity
assert boot_id()
assert process_identity(os.getpid())['group'] == os.getpgrp()
''')


def test_bridge_environment_and_archive_operations_work_without_service(tmp_path):
    run_isolated(tmp_path, r'''
import importlib.abc
import os
from pathlib import Path
import sys
BLOCKED = ('sico.service', 'sico.providers', 'sico_ui', 'PyQt5', 'PySide')
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + '.') for p in BLOCKED):
            raise ImportError('forbidden dependency: ' + fullname)
sys.meta_path.insert(0, Block())
from sico.transport.platform_environment import capture_environment
assert capture_environment({'PATH': '/eda/bin', 'SICO_API_KEY': 'excluded'}) == {'PATH': '/eda/bin'}
from sico.core.contracts import BoundContext
from sico.transport.bridge_client import BridgeClient
from sico.transport.instance_bridge import InstanceBridge
from sico.transport.router_archive import capture_archive_owner, maintain_router_logs
os.environ.clear()
os.environ.update(PATH='/host/bin', SICO_VIRTUOSO_PATH='/eda/bin', SICO_API_KEY='excluded')
context = BoundContext('instance', 'generation', 'bound', {})
owner = capture_archive_owner('v' + str(os.getpid()), allow_self=True)
assert owner['process']['pid'] == os.getpid()
with InstanceBridge(Path.cwd(), context, archive_owner=owner) as bridge:
    with BridgeClient(bridge.descriptor) as client:
        assert client.platform_environment() == {'PATH': '/eda/bin'}
report = maintain_router_logs(Path.cwd(), apply=True)
assert not report['errors']
assert report['generations'][0]['status'] == 'host_active'
assert not any(name == p or name.startswith(p + '.') for name in sys.modules for p in BLOCKED)
''')
