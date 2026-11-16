"""Emergency stop pins one owned generation and survives an unresponsive service."""

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

import pytest
import sico.service.service_force_stop as stopping
from agent_service_lifecycle_fixtures import service_process
from sico.service.service_discovery import discover_project
from sico.service.service_management import exchange_service
from sico.service.service_messages import ServiceRequest
from test_agent_service_shutdown_process import open_demo


def test_force_stop_bypasses_live_sessions_and_restart_preserves_history(tmp_path):
    with service_process(tmp_path) as (process, descriptor), ExitStack() as stack:
        api, token = open_demo(stack, descriptor, tmp_path)
        api.command(token, "submit", "recorded task").result(5)
        journal = tmp_path / ".sico/ai/agent/sessions/live/events.jsonl"
        before = journal.read_bytes()
        assert exchange_service(descriptor, ServiceRequest("service.status"),
            deadline=time.monotonic() + 5).status.pending.sessions >= 1
        result = api.force_stop_service().result(11)
        assert result.service_id == descriptor.service_id and not result.escalated
        assert process.wait(timeout=2) in (0, 3)
        assert journal.read_bytes().startswith(before)
        assert discover_project(tmp_path).state == "inactive"
    with service_process(tmp_path) as (replacement, fresh):
        assert fresh.service_id != descriptor.service_id and replacement.poll() is None
        # An old desktop's stop must never terminate a replacement generation.
        with pytest.raises(ValueError, match="身份已变化"):
            stopping.force_stop(tmp_path, descriptor, deadline=time.monotonic() + 2)
        assert replacement.poll() is None and journal.read_bytes().startswith(before)


def test_unresponsive_service_is_killed_without_listener_or_journal_rewrite(tmp_path, monkeypatch):
    monkeypatch.setattr(stopping, "GRACE_SECONDS", .15)
    with service_process(tmp_path) as (process, descriptor), ExitStack() as stack:
        api, token = open_demo(stack, descriptor, tmp_path)
        journal = tmp_path / ".sico/ai/agent/sessions/live/events.jsonl"
        before = journal.read_bytes()
        os.kill(process.pid, signal.SIGSTOP)
        result = stopping.ForceServiceStop(tmp_path, descriptor).ready.result(3)
        assert result.escalated and process.wait(timeout=2) == -signal.SIGKILL
        assert journal.read_bytes() == before
        assert discover_project(tmp_path).state == "inactive"
    with service_process(tmp_path) as (replacement, fresh):
        assert fresh.service_id != descriptor.service_id and replacement.poll() is None
        assert journal.read_bytes() == before


@pytest.mark.parametrize("damage", ["generation", "start", "host", "owner"])
def test_unverified_target_is_never_signalled(tmp_path, monkeypatch, damage):
    with service_process(tmp_path) as (process, descriptor):
        target = descriptor
        if damage == "generation":
            target = replace(descriptor, service_id="f" * 32)
        elif damage == "start":
            target = replace(descriptor, process_start="0")
        elif damage == "host":
            target = replace(descriptor, host=replace(descriptor.host, host_id="f" * 32))
        else:
            original = stopping.read_record
            monkeypatch.setattr(stopping, "read_record", lambda _path: {})
        with pytest.raises(ValueError):
            stopping.force_stop(tmp_path, target, deadline=time.monotonic() + 2)
        assert process.poll() is None
        if damage == "owner":
            monkeypatch.setattr(stopping, "read_record", original)


def test_generation_rechecked_after_process_is_pinned(tmp_path, monkeypatch):
    with service_process(tmp_path) as (process, descriptor):
        original = stopping.discover_project
        calls = []
        def changed(project):
            found = original(project)
            calls.append(True)
            return (found if len(calls) == 1 else
                    replace(found, service=replace(descriptor, service_id="f" * 32)))
        monkeypatch.setattr(stopping, "discover_project", changed)
        with pytest.raises(ValueError, match="身份已变化"):
            stopping.force_stop(tmp_path, descriptor, deadline=time.monotonic() + 2)
        assert process.poll() is None


def test_cli_force_stop_works_when_status_connection_cannot_reply(tmp_path):
    with service_process(tmp_path) as (process, descriptor):
        os.kill(process.pid, signal.SIGSTOP)
        entry = Path(__file__).absolute().parents[3] / "sico/python/sico-entry"
        result = subprocess.run([sys.executable, str(entry), "service-stop", "--force",
            "--project-dir", str(tmp_path)], capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr
        reply = json.loads(result.stdout)
        assert reply == dict(state="stopped", service_id=descriptor.service_id, escalated=True)
        assert process.wait(timeout=2) == -signal.SIGKILL


def test_closed_service_is_already_stopped(tmp_path):
    with service_process(tmp_path) as (process, descriptor):
        process.terminate()
        process.wait(timeout=5)
        result = stopping.force_stop(tmp_path, descriptor, deadline=time.monotonic() + 2)
        assert result.service_id == descriptor.service_id and not result.escalated
