"""Real old-kernel service ownership, including a frozen service and lost owner."""

import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from sico.service import service_force_stop
from sico.service.service_discovery import discover_project
from sico.transport.framing import Connection


@contextmanager
def owned_service(project):
    parent, child = socket.socketpair()
    code = '''
import os,sys
if hasattr(os, 'pidfd_open'): del os.pidfd_open
from sico.service.service_entry import run_service
raise SystemExit(run_service(sys.argv[1], bootstrap_fd=int(sys.argv[2])))
'''
    command = [sys.executable, '-s', '-c', code, str(project), str(child.fileno())]
    process = subprocess.Popen(command,
                               pass_fds=(child.fileno(),), stderr=subprocess.PIPE)
    child.close()
    try:
        assert Connection(parent).receive(deadline=time.monotonic() + 10)['kind'] == 'ready'
        descriptor = discover_project(project).service
        assert descriptor and descriptor.pid != process.pid
        yield process, descriptor
    finally:
        parent.close()
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=5)


def legacy(monkeypatch):
    def unavailable():
        raise ValueError('No pidfds')
    monkeypatch.setattr(service_force_stop, 'require_pidfds', unavailable)
    monkeypatch.setattr(service_force_stop, 'GRACE_SECONDS', .1)


@pytest.mark.parametrize('frozen', [False, True])
def test_owned_service_stop_without_pidfds(tmp_path, monkeypatch, frozen):
    legacy(monkeypatch)
    sibling = subprocess.Popen(['/bin/sleep', '30'])
    try:
        with owned_service(tmp_path) as (owner, service):
            if frozen:
                os.kill(service.pid, signal.SIGSTOP)
            result = service_force_stop.force_stop(tmp_path, service, deadline=time.monotonic() + 4)
            assert result.escalated == frozen
            assert owner.wait(3) in (0, 3, 137)
            assert not Path('/proc', str(service.pid)).exists()
            assert sibling.poll() is None
            assert discover_project(tmp_path).state == 'inactive'
    finally:
        sibling.terminate()
        sibling.wait(3)


@pytest.mark.parametrize('damage', ['start', 'generation'])
def test_old_identity_does_not_reach_signal(tmp_path, monkeypatch, damage):
    legacy(monkeypatch)
    with owned_service(tmp_path) as (owner, service):
        stale = replace(service, **({'process_start': '0'} if damage == 'start' else
                                   {'service_id': 'f' * 32}))
        with pytest.raises(ValueError):
            service_force_stop.force_stop(tmp_path, stale, deadline=time.monotonic() + 2)
        assert owner.poll() is None
        assert discover_project(tmp_path).service == service


def test_owner_sigkill_terminates_service_by_kernel_lifetime_binding(tmp_path):
    with owned_service(tmp_path) as (owner, service):
        owner.kill()
        owner.wait(3)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if discover_project(tmp_path).state == 'inactive':
                break
            time.sleep(.02)
        assert discover_project(tmp_path).state == 'inactive'


def test_early_failure_releases_custody_children(tmp_path):
    code = '''
import os
if hasattr(os, 'pidfd_open'): del os.pidfd_open
from sico.service.service_custody import run
from sicoprocess import _children

def failed(*args, **kwargs):
    raise ValueError('controlled startup failure')
try:
    assert run(failed, '.', bootstrap_fd=None, idle_seconds=1) == 1
except ValueError:
    pass
assert not _children(os.getpid())
'''
    result = subprocess.run([sys.executable, '-s', '-c', code], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_cli_starts_without_pidfds_and_exits_after_idle(tmp_path):
    code = '''
import os, sys
for name in ('pidfd_open',):
    if hasattr(os, name): delattr(os, name)
from sico.cli import main
raise SystemExit(main(['project-service', '--project-dir', sys.argv[1],
                      '--idle-seconds', '0.1']))
'''
    result = subprocess.run([sys.executable, '-B', '-s', '-c', code, str(tmp_path)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert discover_project(tmp_path).state == 'inactive'


def test_custody_still_rejects_a_live_worker_before_fork(tmp_path):
    code = '''
import os, threading
if hasattr(os, 'pidfd_open'): del os.pidfd_open
from sico.service.service_custody import run
from sicoprocess import _children
release = threading.Event()
thread = threading.Thread(target=release.wait)
thread.start()
try:
    try:
        run(lambda *args, **kwargs: 0, '.', bootstrap_fd=None, idle_seconds=1)
    except RuntimeError as exc:
        assert 'before service threads' in str(exc)
    else:
        raise AssertionError('Custody forked after a worker started')
    assert not _children(os.getpid())
finally:
    release.set()
    thread.join(2)
'''
    result = subprocess.run([sys.executable, '-B', '-s', '-c', code],
                            capture_output=True, text=True, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr


def test_early_custody_failure_is_reported_on_bootstrap_channel():
    code = '''
import socket, threading
from sico.service import service_custody
service_custody.require_pidfds = lambda: (_ for _ in ()).throw(ValueError('No pidfds'))
from sico.service.service_entry import run_service
from sico.transport.framing import Connection
parent, child = socket.socketpair()
release = threading.Event()
thread = threading.Thread(target=release.wait)
thread.start()
status = run_service('.', bootstrap_fd=child.detach(), idle_seconds=1)
row = Connection(parent).receive()
assert status == 1
assert row['kind'] == 'failed' and row['phase'] == 'ownership'
assert row['code'] == 'RuntimeError'
parent.close()
release.set()
thread.join(2)
'''
    result = subprocess.run([sys.executable, '-B', '-s', '-c', code],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
