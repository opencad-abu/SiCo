"""Private IBus supervision and readiness checks without a real D-Bus session."""

from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from sico_pinyin import bus


class Daemon:
    def __init__(self, directory, *, exit_early=False):
        self.directory = directory
        self.exit_early = exit_early
        self.terminated = 0
        (directory / "ibus.address").write_text("unix:path=/tmp/private", encoding="utf-8")

    def poll(self):
        return 1 if self.exit_early else None

    def terminate(self):
        self.terminated += 1

    def wait(self, timeout=None):
        return 0


def run_result(stdout="", returncode=0):
    return SimpleNamespace(returncode=returncode, stdout=stdout)


def test_supervise_sets_private_xdg_paths_and_cleans_directory(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(bus, "run", lambda control, command, **kwargs: seen.append(
        (control, command, kwargs)
    ) or "status")
    monkeypatch.setattr(bus.os, "close", lambda _fd: None)
    monkeypatch.setattr(bus.shutil, "rmtree", lambda path: seen.append(("rmtree", path)))
    directory = tmp_path / "private"
    directory.mkdir()
    assert bus.supervise("control", directory) == "status"
    assert seen[0][1][:2] == ["dbus-run-session", "--"]
    assert seen[0][1][-1] == str(directory)
    assert seen[0][2]["forward_status"] is True
    assert seen[-1] == ("rmtree", directory)


def test_serve_selects_libpinyin_and_writes_ready(monkeypatch, tmp_path):
    calls = []
    daemon = None

    def popen(command, **kwargs):
        nonlocal daemon
        calls.append(("popen", command, kwargs))
        daemon = Daemon(tmp_path)
        return daemon

    def run(command, **kwargs):
        calls.append(("run", command, kwargs))
        if command[1] == "list-engine":
            return run_result(" libpinyin - IBus libpinyin\n")
        if command[1] == "engine":
            return run_result("libpinyin\n")
        return run_result()

    monkeypatch.setattr(bus.subprocess, "Popen", popen)
    monkeypatch.setattr(bus.subprocess, "run", run)
    bus.serve(tmp_path)
    assert (tmp_path / "ready").read_text() == "ready\n"
    assert any(call[0] == "popen" for call in calls)
    assert daemon.terminated == 1
    settings = [call for call in calls if call[0] == "run" and len(call[1]) > 1
                and call[1][0] == "gsettings"]
    assert len(settings) == 3
    assert settings[0][2]["env"]["GSETTINGS_BACKEND"] == "dconf"
    assert "IBUS_ADDRESS" not in settings[0][2]["env"]


@pytest.mark.parametrize("mode", ["early", "no_engine"])
def test_serve_fails_closed_when_daemon_or_engine_is_unavailable(
    monkeypatch, tmp_path, mode
):
    class Early(Daemon):
        def __init__(self, directory):
            super().__init__(directory, exit_early=True)

    def popen(_command, **_kwargs):
        return Early(tmp_path)

    def run(command, **kwargs):
        if command[1] == "list-engine":
            return run_result(" no-libpinyin\n")
        return run_result()

    monkeypatch.setattr(bus.subprocess, "Popen", popen)
    monkeypatch.setattr(bus.subprocess, "run", run)
    with pytest.raises(RuntimeError):
        bus.serve(tmp_path)
