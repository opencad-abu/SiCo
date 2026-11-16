"""Local process custody and SSH transport must not load Codex or service runtimes."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check_rule, load_manifest

ROOT = Path(__file__).resolve().parents[3]
RULES = {row["id"]: row for row in load_manifest()["rules"]}


@pytest.mark.parametrize("name", [
    "local-process-application-boundary", "diagnostics-runtime-free",
    "transport-codex-one-way-boundary",
])
def test_sources_obey_boundary(name):
    assert not check_rule(RULES[name], root=ROOT)


@pytest.mark.parametrize("source", [
    "from ..codex.local_process import LocalProcess",
    "def start():\n    from sico.codex.local_process import LocalProcess",
    "import sico.codex.rpc as rpc",
])
def test_transport_cannot_reintroduce_codex_even_inside_functions(source):
    assert violations(source, RULES["transport-codex-one-way-boundary"],
                      module="sico.transport.bridge_tunnel")


@pytest.mark.parametrize("module", ["transport", "service", "codex", "providers", "storage"])
def test_shared_owner_cannot_reach_application_consumers(module):
    source = f"def start():\n    import sico.{module}"
    assert violations(source, RULES["local-process-application-boundary"],
                      module="sico.local_process")


def test_real_local_owner_and_tunnel_run_without_application_packages(tmp_path):
    # A local fixture substitutes the SSH executable; it opens no network link,
    # Service or EDA process. The real supervisor, pipes and Unix socket run.
    ssh = tmp_path / "ssh"
    ssh.write_text(f"#!{sys.executable}\n" + '''
import socket, sys, time
path = sys.argv[sys.argv.index('-L') + 1].split(':', 1)[0]
server = socket.socket(socket.AF_UNIX)
server.bind(path)
server.listen(1)
connection, _ = server.accept()
connection.sendall(b'ready')
connection.close()
time.sleep(30)
''')
    ssh.chmod(0o700)
    roots = [str(ROOT / path) for path in ("tools/sico/python", "tools/common/python")]
    script = r'''
import importlib.abc
import os
from pathlib import Path
import select
import sys
import time
BLOCKED = ('sico.service', 'sico.codex', 'sico.providers', 'sico.storage',
           'cadai', 'sico_ui', 'PyQt5', 'PySide', 'httpx', 'requests')
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + '.') for p in BLOCKED):
            raise ImportError('forbidden dependency: ' + fullname)
sys.meta_path.insert(0, Block())
from sico.local_process import LocalProcess
from sico.transport.bridge_tunnel import BridgeTunnel
environment = dict(os.environ)
owner = LocalProcess([sys.executable, '-B', '-S', '-c',
    "import time; print('ready', flush=True); time.sleep(30)"], environment, Path.cwd())
try:
    assert select.select([owner.child.stdout], [], [], 5)[0]
    assert owner.child.stdout.readline() == b'ready\n'
finally:
    owner.close()
    assert not owner.child.stdout.closed
    owner.child.stdout.close()
    owner.close()
assert owner.child.poll() == 0
assert environment == dict(os.environ)
tunnel = BridgeTunnel('remote-node', 1234, deadline=time.monotonic() + 5)
directory = tunnel.directory
process = tunnel.process
try:
    with tunnel.connect(3) as connection:
        assert connection.recv(5) == b'ready'
finally:
    tunnel.close()
    tunnel.close()
assert process.child.poll() == 0 and process.child.stdout.closed
assert not directory.exists()
assert not any(name == p or name.startswith(p + '.') for name in sys.modules for p in BLOCKED)
'''
    environment = dict(os.environ, SICO_HOME=str(ROOT), PYTHONPATH=os.pathsep.join(roots),
                       PATH=str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    environment.pop("CAD_HOME", None)
    result = subprocess.run([sys.executable, "-B", "-S", "-c", script],
                            cwd=tmp_path, env=environment, capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
