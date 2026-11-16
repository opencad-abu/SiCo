"""Terminal pipe, startup and shutdown ownership regressions."""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from cad_ai_terminal.control_channel import ControlChannel
from terminal_fixtures import PYQT_ROOT


class Notifier:
    Read = 1

    def __init__(self, *args):
        self.activated = SimpleNamespace(connect=lambda callback: None)
        self.enabled = True
        self.deleted = False

    def setEnabled(self, enabled):
        self.enabled = enabled

    def deleteLater(self):
        self.deleted = True


@pytest.mark.parametrize("ending", [b"shutdown\n", b"", b"x" * 65537])
def test_control_channel_releases_pipe_before_one_shutdown(monkeypatch, ending):
    read_fd, write_fd = os.pipe()
    monkeypatch.setenv("CAD_AI_CONTROL_FD", str(read_fd))
    events = []

    def shutdown():
        with pytest.raises(OSError):
            os.fstat(read_fd)
        events.append("shutdown")

    channel = ControlChannel(None, lambda: events.append("show"), shutdown, Notifier)
    notifier = channel._notifier
    try:
        os.write(write_fd, b"sh")
        channel._read_available()
        assert not events
        os.write(write_fd, b"ow\n")
        channel._read_available()
        assert events == ["show"]
        # Write/read bounded chunks to avoid a blocking pipe for oversized input.
        for start in range(0, len(ending), 1024):
            if channel._fd is None:
                break
            os.write(write_fd, ending[start : start + 1024])
            channel._read_available()
        os.close(write_fd)
        write_fd = None
        channel._read_available()
        channel.close()
        channel._read_available()
        assert events == ["show", "shutdown"]
        assert not notifier.enabled and notifier.deleted
    finally:
        channel.close()
        if write_fd is not None:
            os.close(write_fd)


def test_frontend_import_remains_stdlib_only_in_fresh_interpreter():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from cad_ai_terminal import qt_frontend; "
            "assert not any(n.startswith(('PyQt5', 'QTermWidget')) for n in sys.modules)",
        ],
        env={**os.environ, "PYTHONPATH": str(PYQT_ROOT)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_shutdown_stops_every_session_before_force_release_and_close(monkeypatch):
    from cad_ai_terminal import window_shutdown

    class Timer:
        def __init__(self, parent):
            self.timeout = SimpleNamespace(connect=lambda callback: setattr(self, "fire", callback))
            self.delay = None

        def setSingleShot(self, value):
            assert value

        def start(self, delay):
            self.delay = delay

        def stop(self):
            self.delay = None

    events = []
    sessions = [
        SimpleNamespace(
            begin_shutdown=lambda n=n: events.append(("hup", n)),
            force_shutdown=lambda n=n: events.append(("kill", n)),
        )
        for n in (1, 2)
    ]
    monkeypatch.setattr(window_shutdown, "QTimer", Timer)
    owner = window_shutdown.WindowShutdown(
        None,
        lambda: tuple(sessions),
        lambda: events.append("release"),
        lambda: events.append("close"),
    )
    owner.begin()
    owner.begin()
    assert events == [("hup", 1), ("hup", 2)]
    assert owner.active and owner._grace.delay == 1200
    owner._grace.fire()
    assert events[-2:] == [("kill", 1), ("kill", 2)]
    assert owner._settle.delay == 100
    owner._settle.fire()
    assert events[-2:] == ["release", "close"]
    assert not owner.active
    owner.stop()
    assert owner._grace.delay is None and owner._settle.delay is None
