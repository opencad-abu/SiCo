"""Real local trees: escaped groups, orphan adoption, owner death and siblings."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from agent_service_launch_fixtures import eventually

from sico.local_process import LocalProcess
from sico.codex.runtime_scratch import RuntimeScratch

TREE = '''
import os, signal, sys, time
from pathlib import Path
root = Path(sys.argv[1])
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        (root / 'orphan').write_text(str(os.getpid()))
        time.sleep(30)
    os._exit(0)
(root / 'leader').write_text(str(os.getpid()))
time.sleep(30)
'''


@pytest.mark.parametrize("trigger", ["close", "leader_exit"])
def test_supervisor_reaps_detached_orphans_without_touching_sibling(tmp_path, trigger):
    sibling = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    owner = LocalProcess([sys.executable, "-c", TREE, str(tmp_path)], dict(os.environ), tmp_path)
    try:
        eventually(lambda: (tmp_path / "orphan").exists() and (tmp_path / "leader").exists())
        pids = [int((tmp_path / name).read_text()) for name in ("leader", "orphan")]
        if trigger == "leader_exit":
            os.kill(pids[0], signal.SIGKILL)
        start = time.monotonic()
        owner.close()
        assert time.monotonic() - start < 2.5
        assert all(not Path("/proc", str(pid)).exists() for pid in pids)
        assert sibling.poll() is None
    finally:
        owner.close()
        sibling.terminate()
        sibling.wait(3)


def test_owner_sigkill_closes_control_pipe_and_reaps_local_tree(tmp_path):
    code = '''
import os, sys, time
from pathlib import Path
from sico.local_process import LocalProcess
process = LocalProcess([sys.executable, '-c', sys.argv[2], sys.argv[1]],
                       dict(os.environ), sys.argv[1])
Path(sys.argv[1], 'guard').write_text(str(process.child.pid))
time.sleep(30)
'''
    owner = subprocess.Popen([sys.executable, "-c", code, str(tmp_path), TREE])
    try:
        eventually(lambda: (tmp_path / "orphan").exists() and (tmp_path / "leader").exists())
        pids = [int((tmp_path / name).read_text()) for name in ("leader", "orphan")]
        owner.kill()
        owner.wait(3)
        eventually(lambda: all(not Path("/proc", str(pid)).exists() for pid in pids), 4)
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(3)


def test_scratch_only_removes_its_own_runtime_after_verified_cleanup(tmp_path):
    home = tmp_path / "codex"
    home.mkdir(mode=0o700)
    history = home / "history.jsonl"
    history.write_text("retained")
    first, other = RuntimeScratch(home), RuntimeScratch(home)
    secret = first.path / "socket-token"
    secret.write_text("transient")
    (first.path / "outside").symlink_to(history)
    first.close()
    assert not first.path.exists() and other.path.exists()
    assert history.read_text() == "retained"
    first.close()
    other.close()
