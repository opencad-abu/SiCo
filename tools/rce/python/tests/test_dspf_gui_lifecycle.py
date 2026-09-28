from __future__ import annotations

import io
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dspf_gui_test_support import application
import rcepy.dspf_gui.app as app_module
from rcepy.dspf_gui.app import capture_parent_identity, install_parent_monitor
from rcepy.dspf_gui.bridge import StdioBridge
from rcepy.dspf_gui.oa_actions import OaBridgeMixin


def _write_proc_stat(
    proc_root: Path,
    process_id: int,
    start_time: str,
    *,
    parent_pid: int = 0,
) -> Path:
    stat_path = proc_root / str(process_id) / "stat"
    stat_path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["S", str(parent_pid), *("0" for _ in range(17)), start_time]
    stat_path.write_text(
        f"{process_id} (virtuoso (main)) {' '.join(fields)}\n", encoding="ascii"
    )
    return stat_path


def test_shell_launch_does_not_install_a_parent_monitor() -> None:
    assert capture_parent_identity({}) is None


def test_only_an_owned_gui_uses_the_hard_exit_fallback() -> None:
    exits = []
    identity = (12345, "987654")

    assert app_module._finalize_gui_exit(7, None, exits.append) == 7
    assert exits == []
    assert app_module._finalize_gui_exit(9, identity, exits.append) == 9
    assert exits == [9]


def test_parent_pid_rejects_the_gui_process_itself() -> None:
    with pytest.raises(RuntimeError, match="another process"):
        capture_parent_identity({"RCE_DSPF_PARENT_PID": str(os.getpid())})


def test_parent_pid_rejects_a_process_that_is_not_an_ancestor(
    tmp_path: Path,
) -> None:
    _write_proc_stat(tmp_path, 12345, "987654")
    with pytest.raises(RuntimeError, match="ancestor"):
        capture_parent_identity({"RCE_DSPF_PARENT_PID": "12345"}, proc_root=tmp_path)


def test_parent_pid_accepts_a_virtuoso_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrapper_pid = 23456
    virtuoso_pid = 12345
    _write_proc_stat(tmp_path, wrapper_pid, "111111", parent_pid=virtuoso_pid)
    _write_proc_stat(tmp_path, virtuoso_pid, "987654", parent_pid=1)
    monkeypatch.setattr(app_module.os, "getppid", lambda: wrapper_pid)

    assert capture_parent_identity(
        {"RCE_DSPF_PARENT_PID": str(virtuoso_pid)}, proc_root=tmp_path
    ) == (virtuoso_pid, "987654")


@pytest.mark.parametrize("value", ["", "0", "1", "-2", "12x", "１２"])
def test_parent_pid_rejects_non_positive_or_non_ascii_values(
    tmp_path: Path,
    value: str,
) -> None:
    with pytest.raises(RuntimeError, match="RCE_DSPF_PARENT_PID"):
        capture_parent_identity({"RCE_DSPF_PARENT_PID": value}, proc_root=tmp_path)


def test_parent_monitor_closes_on_pid_reuse(tmp_path: Path) -> None:
    app = application()
    process_id = os.getppid()
    stat_path = _write_proc_stat(tmp_path, process_id, "987654")
    identity = capture_parent_identity(
        {"RCE_DSPF_PARENT_PID": str(process_id)}, proc_root=tmp_path
    )
    closed = []
    timer = install_parent_monitor(
        app, identity, lambda: closed.append(True), proc_root=tmp_path, interval_ms=5
    )
    assert timer is not None and timer.isActive()

    stat_path.write_text(
        stat_path.read_text(encoding="ascii").replace("987654", "987655"),
        encoding="ascii",
    )
    deadline = time.monotonic() + 1.0
    while not closed and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)

    assert closed == [True]
    assert timer.isActive() is False


def test_stdio_eof_emits_bridge_disconnected() -> None:
    application()
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "rb", buffering=0)
    try:
        bridge = StdioBridge(reader=reader, writer=io.BytesIO(), install_notifier=False)
        bridge._fd = read_fd
        bridge._was_blocking = os.get_blocking(read_fd)
        disconnected = []
        bridge.disconnected.connect(lambda: disconnected.append(True))
        os.close(write_fd)
        write_fd = -1

        bridge._read_ready()

        assert disconnected == [True]
        assert bridge._fd is None
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        reader.close()


def test_bridge_disconnect_closes_the_bridge_owned_window() -> None:
    class Harness(OaBridgeMixin):
        bridge = object()
        _bridge_enabled = True
        _pending = {1: "selection"}
        highlight_queries = type("Queries", (), {"invalidate": lambda self: None})()

        def __init__(self) -> None:
            self.closed = 0

        def _set_status(self, _text: str) -> None:
            pass

        def _update_actions(self) -> None:
            pass

        def close(self) -> None:
            self.closed += 1

    harness = Harness()
    harness._bridge_disconnected()

    assert harness.bridge is None
    assert harness._pending == {}
    assert harness.closed == 1


def _process_identity(process_id: int) -> tuple[str, str] | None:
    try:
        stat = Path(f"/proc/{process_id}/stat").read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return None
    _, separator, fields_text = stat.rpartition(") ")
    fields = fields_text.split() if separator else []
    if len(fields) <= 19:
        return None
    return fields[0], fields[19]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires Linux /proc")
def test_busy_owned_gui_hard_exits_after_parent_loss() -> None:
    python_root = Path(__file__).resolve().parents[1]
    gui_code = "\n".join(
        (
            "import os, time",
            'os.environ["QT_QPA_PLATFORM"]="offscreen"',
            "import rcepy.dspf_gui.app as gui_app",
            "from rcepy.dspf_gui import main_window",
            "class BusyWindow(main_window.DspfMainWindow):",
            "    def show(self):",
            "        super().show()",
            "        self.summary_queries.submit(lambda: time.sleep(30))",
            "        print(f'READY {os.getpid()}', flush=True)",
            "main_window.DspfMainWindow=BusyWindow",
            "gui_app.run_gui()",
        )
    )
    owner_code = "\n".join(
        (
            "import os, subprocess, sys",
            "environment=os.environ.copy()",
            'environment["RCE_DSPF_PARENT_PID"]=str(os.getpid())',
            "analyzer=subprocess.Popen(",
            "    [sys.executable, '-c', sys.argv[1]], env=environment)",
            "print(f'PID {analyzer.pid}', flush=True)",
            "raise SystemExit(analyzer.wait())",
        )
    )
    environment = os.environ.copy()
    common_python_root = Path(__file__).resolve().parents[3] / "common" / "python"
    prior_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(python_root), str(common_python_root))
    ) + (os.pathsep + prior_python_path if prior_python_path else "")
    owner = subprocess.Popen(
        [sys.executable, "-c", owner_code, gui_code],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    analyzer_pid = None
    analyzer_identity = None
    try:
        assert owner.stdout is not None
        ready, _, _ = select.select([owner.stdout], [], [], 10.0)
        assert ready, "Analyzer owner did not report its child PID"
        pid_line = owner.stdout.readline().strip().split()
        assert len(pid_line) == 2 and pid_line[0] == "PID"
        analyzer_pid = int(pid_line[1])
        ready, _, _ = select.select([owner.stdout], [], [], 10.0)
        assert ready, "Analyzer did not report readiness"
        assert owner.stdout.readline().strip() == f"READY {analyzer_pid}"
        analyzer_identity = _process_identity(analyzer_pid)
        assert analyzer_identity is not None

        owner.send_signal(signal.SIGTERM)
        owner.wait(timeout=5)
        deadline = time.monotonic() + 7.0
        while time.monotonic() < deadline:
            current = _process_identity(analyzer_pid)
            if (
                current is None
                or current[0] == "Z"
                or current[1] != analyzer_identity[1]
            ):
                break
            time.sleep(0.05)
        else:
            pytest.fail("busy Analyzer survived owner process exit")
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        if analyzer_pid is not None:
            current = _process_identity(analyzer_pid)
            if (
                current is not None
                and current[0] != "Z"
                and analyzer_identity is not None
                and current[1] == analyzer_identity[1]
            ):
                os.kill(analyzer_pid, signal.SIGKILL)
