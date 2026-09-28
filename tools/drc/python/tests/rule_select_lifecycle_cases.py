"""Rule selector lifecycle regressions."""

from __future__ import annotations
from pathlib import Path
import time
import pytest
from drcpy.rule_select_qt import QApplication
from cadgui.lifecycle import finalize_gui_exit as _finalize_gui_exit
from drcpy.rule_select_lifecycle import capture_parent_identity
from cadgui.lifecycle import install_parent_monitor
from cadgui.environment import prepare_qt_environment


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


def test_parent_monitor_closes_when_pid_is_reused(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrapper_pid, virtuoso_pid = 23456, 12345
    _write_proc_stat(tmp_path, wrapper_pid, "111111", parent_pid=virtuoso_pid)
    stat_path = _write_proc_stat(tmp_path, virtuoso_pid, "987654", parent_pid=1)
    monkeypatch.setattr("drcpy.rule_select_lifecycle.os.getppid", lambda: wrapper_pid)
    identity = capture_parent_identity(virtuoso_pid, proc_root=tmp_path)
    closed: list[bool] = []
    timer = install_parent_monitor(
        application,
        identity,
        lambda: closed.append(True),
        proc_root=tmp_path,
        interval_ms=5,
    )
    assert timer is not None and timer.isActive()

    stat_path.write_text(
        stat_path.read_text(encoding="ascii").replace("987654", "987655"),
        encoding="ascii",
    )
    deadline = time.monotonic() + 1
    while not closed and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.01)

    assert closed == [True]
    assert timer.isActive() is False


def test_only_parent_owned_gui_uses_hard_exit_fallback() -> None:
    exits: list[int] = []

    assert _finalize_gui_exit(3, None, exits.append) == 3
    assert exits == []
    assert _finalize_gui_exit(7, (12345, "987654"), exits.append) == 7
    assert exits == [7]


def test_qt_environment_cleanup_preserves_platform() -> None:
    environment = {
        "QT_QPA_PLATFORM_PLUGIN_PATH": "/virtuoso/qt/plugins",
        "QT_PLUGIN_PATH": "/virtuoso/qt",
        "QT_DIR": "/virtuoso/qt",
        "QT_SELECT": "qt5",
        "QML2_IMPORT_PATH": "/virtuoso/qml",
        "QT_XCB_NO_XI2": "1",
        "QT_XCB_NO_XI2_MOUSE": "1",
        "QT_QPA_PLATFORM": "offscreen",
        "LD_LIBRARY_PATH": "/python/lib",
    }

    removed = prepare_qt_environment(environment)

    assert set(removed) == {
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QT_PLUGIN_PATH",
        "QT_DIR",
        "QT_SELECT",
        "QML2_IMPORT_PATH",
        "QT_XCB_NO_XI2",
        "QT_XCB_NO_XI2_MOUSE",
    }
    assert environment == {
        "QT_QPA_PLATFORM": "offscreen",
        "LD_LIBRARY_PATH": "/python/lib",
    }
