"""LSF control serves Codex and background jobs without service runtime imports."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check_rule, load_manifest

ROOT = Path(__file__).resolve().parents[3]
RULES = {row["id"]: row for row in load_manifest()["rules"]}


@pytest.mark.parametrize("name", ["lsf-no-application-runtime", "codex-gateway-no-service"])
def test_sources_obey_boundary(name):
    assert not check_rule(RULES[name], root=ROOT)


@pytest.mark.parametrize("module", ["service", "codex", "transport", "storage", "providers"])
def test_shared_control_rejects_deferred_application_imports(module):
    assert violations("def run():\n    import sico." + module,
                      RULES["lsf-no-application-runtime"], module="sico.lsf.commands")


def test_gateway_cannot_reimport_service_inside_functions():
    assert violations("def run():\n    from ..service.lsf_scheduler import LsfScheduler",
                      RULES["codex-gateway-no-service"], module="sico.codex.gateway_job")


def test_real_gateway_uses_local_scheduler_fixture_without_service_runtime(tmp_path):
    # Only these local executable fixtures run; no site scheduler or remote job.
    scheduler = f"#!{sys.executable}\n" + r'''
import json, os, pathlib, sys, time
name = pathlib.Path(sys.argv[0]).name
assert not any(k in os.environ for k in ('SICO_API_KEY', 'OTHER_SECRET'))
state = pathlib.Path(__file__).parent / 'cancelled'
if name == 'lsid':
    print('My cluster name is fixture')
elif name == 'bsub':
    print('ready', flush=True)
    time.sleep(30)
elif name == 'bkill':
    state.write_text(json.dumps(sys.argv[1:]))
    print('requested')
elif name == 'bjobs':
    query = sys.argv[sys.argv.index('-J') + 1]
    if query == 'sico-probe-no-job':
        print('No job found')
    else:
        import pwd
        print(json.dumps(dict(RECORDS=[dict(JOBID='42', USER=pwd.getpwuid(os.getuid()).pw_name,
            STAT='DONE' if state.exists() else 'RUN', JOB_NAME=query)])))
else:
    raise AssertionError(name)
'''
    for name in ("bsub", "bjobs", "bkill", "bhist", "lsid"):
        path = tmp_path / name
        path.write_text(scheduler)
        path.chmod(0o700)
    script = r'''
import importlib.abc
import json
import os
from pathlib import Path
import select
import socket
import sys

BLOCKED = ('sico.service', 'sico.transport', 'sico.providers', 'sico_ui',
           'PyQt5', 'PySide', 'requests', 'httpx')
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + '.') for p in BLOCKED):
            raise ImportError('forbidden runtime import: ' + fullname)
sys.meta_path.insert(0, Block())
def forbidden(*a, **kw):
    raise AssertionError('no network connection in scheduler boundary fixture')
socket.socket = forbidden
os.environ.clear()
from sico.codex.gateway_job import GatewayJob
from sico.lsf.job_control import LsfJobControl

root = Path.cwd()
environment = dict(SICO_CODEX_LSF_EXE=str(root / 'bsub'), SICO_CODEX_LSF_ARGS='-Is -q fixture',
                   SICO_PYTHON=sys.executable, SICO_API_KEY='sentinel', OTHER_SECRET='sentinel')
job = GatewayJob(environment, root, root / 'evidence')
try:
    assert type(job.scheduler) is LsfJobControl and not hasattr(job.scheduler, 'submit')
    assert select.select([job.process.child.stdout], [], [], 5)[0]
    assert job.process.child.stdout.readline() == b'ready\n'
finally:
    job.close()
assert job.process.child.poll() == 0 and job.process.child.stdout.closed
assert json.loads((root / 'cancelled').read_text()) == ['-J', job.expected['name'], '0']
assert job.record['phase'] == 'closed' and job.record['observation']['state'] == 'DONE'
before = job.path.read_bytes()
job.close()
assert job.path.read_bytes() == before and job.path.stat().st_mode & 0o777 == 0o600
assert 'sentinel' not in before.decode() and environment['SICO_API_KEY'] == 'sentinel'
assert not any(n == p or n.startswith(p + '.') for n in sys.modules for p in BLOCKED)
'''
    roots = [str(ROOT / p) for p in ("tools/sico/python", "tools/ai/python", "tools/common/python")]
    result = subprocess.run([sys.executable, "-B", "-S", "-c", script], cwd=tmp_path,
                            env=dict(os.environ, PYTHONPATH=os.pathsep.join(roots)),
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
