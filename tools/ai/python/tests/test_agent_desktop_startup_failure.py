"""A refused desktop startup reaches the launch parent without a traceback."""

from __future__ import annotations

import io
import json
import os
import threading
from types import SimpleNamespace

import pytest
import sico.service.launch as launch_module
from sico.core.contracts import BoundContext
from sico.service.launch import DesktopPipe
from sico.service.window_lease import CIW_MESSAGE


class _Child:
    def __init__(self, payload=b""):
        read_fd, write_fd = os.pipe()
        if payload:
            os.write(write_fd, payload)
        os.close(write_fd)
        self.stdout = os.fdopen(read_fd, "rb")
        self.stdin = io.BytesIO()
        self.returncode = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


@pytest.fixture
def launch_child(monkeypatch, tmp_path):
    def run(payload, *, status=0):
        child = _Child(payload)
        child.returncode = status
        monkeypatch.setattr(launch_module.subprocess, "Popen", lambda *_a, **_k: child)
        monkeypatch.setattr(launch_module, "agent_command", lambda *_a: ["/bin/true"])
        monkeypatch.setattr(launch_module, "desktop_environment",
                            lambda *_a, **_k: {"TMPDIR": str(tmp_path)})
        context = BoundContext("instance", "generation", "target", {"cwd": str(tmp_path)})
        registration = {
            "context": context.record(),
            "bridge": {"instance_id": "instance", "generation": "generation",
                       "bridge_id": "bridge", "router_id": "router"},
        }
        reader = SimpleNamespace(closed=threading.Event(), close=lambda **_kwargs: None)
        control = DesktopPipe()
        try:
            result = launch_module._run_desktop_child(
                SimpleNamespace(provider_config=None), reader, control, registration,
                context, "session", tmp_path, threading.Lock(), threading.Event())
        finally:
            control.close()
        return result

    return run


def test_existing_window_refusal_is_one_quiet_ciw_line(launch_child, capsys):
    payload = b'{"kind":"startup_failed","phase":"window"}\n'
    assert launch_child(payload) == 0
    captured = capsys.readouterr()
    assert captured.err == CIW_MESSAGE + "\n"
    assert "Traceback" not in captured.err


def test_other_user_facing_startup_failures_do_not_raise(launch_child, capsys):
    payload = b'{"kind":"startup_failed","phase":"config"}\n'
    assert launch_child(payload) == 0
    assert capsys.readouterr().err == ""


def test_failure_after_the_ready_handshake_still_ends_quietly(launch_child):
    ready = json.dumps({
        "kind": "ready", "bridge_id": "bridge", "router_id": "router",
        "instance_id": "instance", "generation": "generation",
        "session_id": "session", "target_id": "target",
    }).encode() + b"\n"
    payload = ready + b'{"kind":"startup_failed","phase":"config"}\n'
    assert launch_child(payload) == 0


def test_cancelled_desktop_before_ready_is_not_an_error(launch_child):
    assert launch_child(b"") == 0


def test_crashed_desktop_before_ready_reports_its_status(launch_child):
    with pytest.raises(RuntimeError, match=r"status 2"):
        launch_child(b"", status=2)
