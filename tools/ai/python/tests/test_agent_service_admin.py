"""Authenticated lifecycle requests across live services and generation changes."""

import json
import signal
import socket
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_service_launch_fixtures import services as services
from agent_service_lifecycle_fixtures import lsf_environment, service_process

from sico.service.service_admin import ServiceAdmin
from sico.service.service_cli import run_admin
from sico.service.service_discovery import discover_project
from sico.service.service_management import exchange_service
from sico.service.service_messages import ServiceRequest
from sico.service.service_ready import probe_ready


def test_status_of_absent_project_is_readonly_and_stop_does_not_launch(tmp_path, capsys):
    args = SimpleNamespace(command="service-status", project_dir=str(tmp_path), timeout=3)
    assert run_admin(args) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "absent"
    args.command = "service-stop"
    assert run_admin(args) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "absent"
    assert not list(tmp_path.iterdir())


def test_stop_receipt_precedes_exit_and_cleanup_is_bounded(tmp_path):
    with service_process(tmp_path, settled=True) as (process, descriptor):
        # Keep an authenticated but incomplete peer so the stop acknowledgement precedes exit.
        with socket.socket(socket.AF_UNIX) as slow:
            slow.connect(descriptor.endpoint)
            slow.sendall(b"\0")
            status = exchange_service(
                descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 2
            ).status.record()
            assert status["state"] == "ready" and status["outcome"] == "observed"
            assert not any(status["pending"].values())
            reply = exchange_service(
                descriptor, ServiceRequest('service.stop'), deadline=time.monotonic() + 2
            ).status.record()
            assert reply["state"] == "stopping" and reply["outcome"] == "stop_requested"
            assert process.poll() is None
            assert process.wait(timeout=3) == 0
        assert not Path(descriptor.endpoint).parent.exists()
        assert discover_project(tmp_path).state == "inactive"


def test_idle_service_exits_without_gui_or_stdin_dependency(tmp_path):
    with service_process(tmp_path, idle_seconds=0.4) as (process, descriptor):
        assert process.wait(timeout=3) == 0
        assert not Path(descriptor.endpoint).parent.exists()
        assert discover_project(tmp_path).state == "inactive"


def test_old_stop_cannot_be_redirected_to_replacement_service(tmp_path, services):
    with service_process(tmp_path, settled=True) as (process, descriptor):
        exchange_service(
            descriptor, ServiceRequest('service.stop'), deadline=time.monotonic() + 2
        ).status.record()
        assert process.wait(3) == 0
    replacement = services(tmp_path).ready.result(10)
    assert replacement.service_id != descriptor.service_id
    request = ServiceAdmin(tmp_path, stop=descriptor)
    assert request.ready.result(3)["state"] == "generation_changed"
    assert request.wait(2)
    assert probe_ready(replacement, deadline=time.monotonic() + 2) == replacement


def test_lsf_attribution_is_bounded_and_stop_does_not_target_allocation(tmp_path):
    with service_process(tmp_path, environment=lsf_environment(), settled=True) as (process, descriptor):
        reply = exchange_service(
            descriptor, ServiceRequest('service.status'), deadline=time.monotonic() + 2
        ).status.record()
        assert reply["allocation"] == dict(
            job_id="test-job", cluster="test-cluster", queue="test-queue")
        assert "never-expose" not in json.dumps(reply)
        discovery = tmp_path / ".sico/ai/agent/service/discovery.json"
        assert "never-expose" not in discovery.read_text()
        exchange_service(
            descriptor, ServiceRequest('service.stop'), deadline=time.monotonic() + 2
        ).status.record()
        assert process.wait(3) == 0


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_scheduler_or_operator_signal_closes_endpoint_and_releases_project(tmp_path, number):
    with service_process(tmp_path, environment=lsf_environment(), settled=True) as (process, descriptor):
        process.send_signal(number)
        assert process.wait(3) == 0
        assert not Path(descriptor.endpoint).parent.exists()
        assert discover_project(tmp_path).state == "inactive"


def test_cli_stop_uses_the_observed_generation(tmp_path, capsys):
    with service_process(tmp_path, settled=True) as (process, _descriptor):
        args = SimpleNamespace(command="service-stop", project_dir=str(tmp_path), timeout=3)
        assert run_admin(args) == 0
        reply = json.loads(capsys.readouterr().out)
        assert reply["state"] == "stopping"
        assert reply["status"]["outcome"] == "stop_requested"
        assert process.wait(3) == 0
