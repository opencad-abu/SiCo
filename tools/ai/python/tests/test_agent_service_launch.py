"""On-demand startup crosses real process boundaries and preserves project ownership."""

import multiprocessing
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from agent_service_launch_fixtures import launch_together, stop_service
from agent_service_launch_fixtures import services as services
from sico.service.service_discovery import discover_project
from sico.service.service_ready import probe_ready
from sico.service.service_startup import ServiceStartup, ServiceStartupError
from sico.storage.project_files import write_record
from sico.transport.framing import Connection


def test_starts_reuses_and_isolates_projects(tmp_path, services):
    first = services(tmp_path).ready.result(10)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    assert services(alias).ready.result(10) == first
    other = tmp_path / "other"
    other.mkdir()
    second = services(other).ready.result(10)
    assert first.pid != second.pid and first.project_id != second.project_id
    assert Path(first.endpoint).parent.stat().st_mode & 0o777 == 0o700
    assert Path(first.endpoint).stat().st_mode & 0o777 == 0o600
    assert not Path(first.endpoint).is_relative_to(tmp_path)


def test_startup_reattaches_after_host_identity_changes_during_same_boot(tmp_path, services):
    first = services(tmp_path).ready.result(10)
    renamed = replace(first.host, host_id="f" * 32, hostname="renamed-node")
    write_record(
        tmp_path / ".sico/ai/agent/service/discovery.json",
        replace(first, host=renamed).record(),
    )
    attached = services(tmp_path).ready.result(10)
    assert attached.service_id == first.service_id
    assert attached.host == renamed


def test_independent_launchers_elect_one_service_and_parent_exit_does_not_stop_it(tmp_path):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(4)
    processes, pipes = [], []
    descriptor = None
    try:
        for _ in range(4):
            parent, child = context.Pipe(duplex=False)
            process = context.Process(target=launch_together, args=(tmp_path, barrier, child))
            process.start()
            child.close()
            processes.append(process)
            pipes.append(parent)
        descriptors = []
        for pipe in pipes:
            assert pipe.poll(15)
            descriptors.append(pipe.recv())
        descriptor = descriptors[0]
        assert all(item == descriptor for item in descriptors)
        for process in processes:
            process.join(5)
            assert process.exitcode == 0
        assert probe_ready(descriptor, deadline=time.monotonic() + 2) == descriptor
        assert os.getsid(descriptor.pid) == descriptor.pid
        assert os.readlink(f"/proc/{descriptor.pid}/fd/0") == "/dev/null"
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
            process.join(5)
        for pipe in pipes:
            pipe.close()
        descriptor = descriptor or discover_project(tmp_path).service
        if descriptor:
            stop_service(descriptor)


def test_cli_service_never_imports_qt_and_survives_bootstrap_eof(tmp_path):
    import socket

    parent, child = socket.socketpair()
    code = """
import sys
class RejectQt:
    def find_spec(self, fullname, *args):
        if fullname.startswith(('PyQt', 'PySide', 'sico_ui')):
            raise AssertionError('GUI import in service: ' + fullname)
sys.meta_path.insert(0, RejectQt())
from sico.cli import main
raise SystemExit(main(sys.argv[1:]))
"""
    env = dict(os.environ)
    env.pop("DISPLAY", None)
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    process = subprocess.Popen([sys.executable, "-c", code, "project-service",
        "--project-dir", str(tmp_path), "--bootstrap-fd", str(child.fileno())],
        env=env, pass_fds=(child.fileno(),), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child.close()
    try:
        row = Connection(parent).receive(deadline=time.monotonic() + 10)
        assert row["kind"] == "ready"
        parent.close()
        descriptor = discover_project(tmp_path).service
        assert probe_ready(descriptor, deadline=time.monotonic() + 2) == descriptor
        assert process.poll() is None
    finally:
        parent.close()
        process.terminate()
        output = process.communicate(timeout=5)
        assert process.returncode == 0, output
    assert not Path(descriptor.endpoint).parent.exists()


def test_launch_failure_is_visible_and_does_not_create_ownership(tmp_path, monkeypatch):
    from sico.service import service_process

    monkeypatch.setattr(service_process, "agent_command", lambda *_args: ["/missing/service"])
    startup = ServiceStartup(tmp_path)
    with pytest.raises(FileNotFoundError):
        startup.ready.result(5)
    assert startup.wait(2)
    assert discover_project(tmp_path).state == "absent"


def test_child_exit_before_handshake_is_not_reported_ready(tmp_path, monkeypatch):
    from sico.service import service_process

    monkeypatch.setattr(service_process, "agent_command", lambda *_args: ["/bin/true"])
    startup = ServiceStartup(tmp_path)
    with pytest.raises(ServiceStartupError, match="channel_disconnected"):
        startup.ready.result(5)
    assert startup.wait(2)


def test_service_startup_requires_explicit_project_and_bounded_wait(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        ServiceStartup(".")
    for timeout in (0, -1, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="finite"):
            ServiceStartup(tmp_path, timeout=timeout)


def test_detached_service_retains_lsf_allocation_environment(tmp_path, monkeypatch, services):
    monkeypatch.setenv("LSB_JOBID", "service-test-job")
    monkeypatch.setenv("LSB_QUEUE", "service-test-queue")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    descriptor = services(tmp_path).ready.result(10)
    environment = Path(f"/proc/{descriptor.pid}/environ").read_bytes().split(b"\0")
    assert b"LSB_JOBID=service-test-job" in environment
    assert b"LSB_QUEUE=service-test-queue" in environment
    assert Path(descriptor.endpoint).parent.parent == Path("/tmp")
