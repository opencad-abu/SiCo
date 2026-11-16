"""Real process contention, abnormal exit and fork/exec lock lifetime tests."""

import errno
import multiprocessing
import os
import subprocess
import sys

import pytest
from agent_service_fixtures import compete, hold, reap, receive

from sico.service.project_service import ProjectBusy, ProjectServiceLease
from sico.service.service_discovery import discover_project
from sico.storage import project_lock
from sico.storage.project_files import RegistryError


def test_simultaneous_processes_elect_one_owner(tmp_path):
    context = multiprocessing.get_context("spawn")
    barrier, release = context.Barrier(5), context.Event()
    children, channels = [], []
    try:
        for _ in range(4):
            parent, child = context.Pipe()
            process = context.Process(target=compete, args=(str(tmp_path), barrier, release, child))
            process.start()
            child.close()
            children.append(process)
            channels.append(parent)
        barrier.wait(timeout=15)
        reports = [receive(channel) for channel in channels]
        assert [r[0] for r in reports].count("owner") == 1, reports
        assert [r[0] for r in reports].count("busy") == 3, reports
        project_id = next(r[1] for r in reports if r[0] == "owner")
    finally:
        release.set()
        for process in children:
            reap(process)
        for channel in channels:
            channel.close()
    assert all(process.exitcode == 0 for process in children)
    with ProjectServiceLease.acquire(tmp_path) as owner:
        assert owner.project_id == project_id


def test_killed_owner_releases_lock_without_pid_or_heartbeat_heuristic(tmp_path):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=hold, args=(str(tmp_path), child))
    process.start()
    child.close()
    try:
        project_id, service_id, pid = receive(parent)
        assert pid == process.pid
        with pytest.raises(ProjectBusy):
            ProjectServiceLease.acquire(tmp_path)
        process.kill()
        reap(process)
        with ProjectServiceLease.acquire(tmp_path) as owner:
            assert owner.project_id == project_id
            assert owner.service_id != service_id
    finally:
        if process.is_alive():
            process.kill()
            reap(process)
        parent.close()


def test_fork_child_drops_inherited_capability_without_unlocking_parent(tmp_path):
    context = multiprocessing.get_context("fork")
    parent, child = context.Pipe()
    with ProjectServiceLease.acquire(tmp_path) as owner:
        def inherited():
            try:
                owner.publish("/does-not-exist")
            except RegistryError as exc:
                child.send(exc.code)
            try:
                ProjectServiceLease.acquire(tmp_path)
            except ProjectBusy:
                child.send("busy")
            child.recv()

        process = context.Process(target=inherited)
        process.start()
        child.close()
        try:
            assert receive(parent) == "ownership_lost"
            assert receive(parent) == "busy"
            assert discover_project(tmp_path).state == "starting"
            owner.close()
            # Child is alive but cannot retain the parent's flock after fork.
            with ProjectServiceLease.acquire(tmp_path):
                pass
        finally:
            parent.send("exit")
            reap(process)
            parent.close()
    assert process.exitcode == 0


def test_exec_does_not_inherit_lock_or_import_qt(tmp_path):
    script = """
import os, sys
from sico.service.project_service import ProjectServiceLease
with ProjectServiceLease.acquire(sys.argv[1]):
    assert not any(name.startswith('PyQt') for name in sys.modules)
print('owned', flush=True)
sys.stdin.readline()
"""
    owner = ProjectServiceLease.acquire(tmp_path)
    process = subprocess.Popen([sys.executable, "-c", "import sys; print('ready', flush=True); "
                                "sys.stdin.readline()"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True, close_fds=False)
    try:
        assert process.stdout.readline().strip() == "ready"
        owner.close()
        result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], input="exit\n",
                                text=True, capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "owned\n"
    finally:
        owner.close()
        process.communicate("exit\n", timeout=5)


@pytest.mark.parametrize("code", [errno.ENOLCK, errno.EOPNOTSUPP, errno.EIO])
def test_unavailable_locking_never_publishes_an_identity(tmp_path, monkeypatch, code):
    def unavailable(*_args):
        raise OSError(code, "locking unavailable")
    monkeypatch.setattr(project_lock.fcntl, "flock", unavailable)
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(RegistryError) as error:
        ProjectServiceLease.acquire(tmp_path)
    assert error.value.code == "lock_unavailable"
    assert not (tmp_path / ".sico/ai/agent/service/project.json").exists()
    assert len(os.listdir("/proc/self/fd")) == before
