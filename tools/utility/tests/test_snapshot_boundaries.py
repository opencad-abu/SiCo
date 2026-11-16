"""Snapshot reading must not depend on provider or service runtime imports."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check_rule, load_manifest

ROOT = Path(__file__).resolve().parents[3]
RULES = {row["id"]: row for row in load_manifest()["rules"]}
BOUNDARIES = ("configuration-no-application-runtime", "session-snapshot-one-way-boundary")


@pytest.mark.parametrize("name", BOUNDARIES)
def test_sources_obey_boundary(name):
    assert not check_rule(RULES[name], root=ROOT)


@pytest.mark.parametrize("source", [
    "from ..providers.config import validate_config",
    "def validate(row):\n    from ..service.background_config import runtime_settings",
    "from ..codex.settings import validate_codex",
])
def test_snapshot_rejects_upward_imports(source):
    assert violations(source, RULES["session-snapshot-one-way-boundary"],
                      module="sico.storage.session_snapshot")


def test_snapshot_roundtrip_without_runtimes_or_credentials(tmp_path):
    roots = [str(ROOT / p) for p in
             ("tools/sico/python", "tools/ai/python", "tools/common/python")]
    script = "import sys\nsys.path[:0] = " + repr(roots) + "\n" + r'''
import importlib.abc
import os
from pathlib import Path
import sys
BLOCKED = ("sico.providers", "sico.service", "sico.codex", "sico_ui", "PyQt5", "PySide",
           "requests", "httpx")
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + ".") for p in BLOCKED):
            raise ImportError("forbidden runtime dependency: " + fullname)
sys.meta_path.insert(0, Block())
def no_process_start(event, args):
    if event in ("subprocess.Popen", "os.system", "os.fork", "os.posix_spawn"):
        raise AssertionError("snapshot validation started a process: " + event)
sys.addaudithook(no_process_start)
os.environ.clear()
from sico.core.contracts import BoundContext
from sico.storage.session_snapshot import capture, read, save, validate
runtime = dict(virtuoso="/missing/virtuoso", context="/missing/runtime.cxt",
               context_sha256="0" * 64, version="fixture", libraries={"Work": "/missing/Work"})
lsf = dict(cluster="test", owner="test", queue="batch", shared_root="/missing/shared",
           node_command=["/missing/python"],
           **{name: "/missing/" + name for name in ("bsub", "bjobs", "bkill", "bhist", "lsid")})
for protocol in ("anthropic", "openai", "responses"):
    config = dict(provider=protocol, base_url="https://example.test/v1", model="fixture")
    if protocol == "responses":
        config["external_resources"] = "/missing/resources.json"
    row = capture(protocol, BoundContext("i", "g", "t", {}), config, {}, {},
                  background=dict(runtime=runtime, lsf=lsf))
    assert validate(row, protocol) is row
    directory = Path.cwd() / "agent/sessions" / protocol
    directory.mkdir(parents=True, mode=0o700)
    for parent in (directory.parent, directory.parent.parent):
        parent.chmod(0o700)
    save(directory, row)
    assert read(directory) == row
assert not any(name == p or name.startswith(p + ".") for name in sys.modules for p in BLOCKED)
'''
    env = dict(os.environ, SICO_HOME=str(ROOT), PYTHONPATH=os.pathsep.join(roots))
    env.pop("CAD_HOME", None)
    result = subprocess.run([sys.executable, "-B", "-S", "-c", script], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
