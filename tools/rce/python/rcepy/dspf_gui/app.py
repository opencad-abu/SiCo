"""PyQt5 application bootstrap with EDA-safe environment handling."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import MutableMapping

import cadgui.environment as _gui_environment
from cadgui.chrome import apply_family_style
from cadgui.lifecycle import (
    capture_parent_identity as _capture_parent_identity,
    finalize_gui_exit as _finalize_gui_exit,
    install_parent_monitor,
)

ctypes = _gui_environment.ctypes
_XCB_RUNTIME_HANDLE: object | None = None
_PARENT_PID_ENV = "RCE_DSPF_PARENT_PID"


def capture_parent_identity(
    environ: MutableMapping[str, str] | None = None,
    *,
    proc_root: Path = Path("/proc"),
) -> tuple[int, str] | None:
    """Capture a SKILL-provided Virtuoso PID and its Linux start time."""
    return _capture_parent_identity(
        None,
        environ,
        parent_env=_PARENT_PID_ENV,
        proc_root=proc_root,
        current_pid=os.getpid(),
        current_parent_pid=os.getppid(),
    )


def prepare_qt_environment(
    environ: MutableMapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Remove Qt search paths inherited from Virtuoso or another EDA tool."""
    return _gui_environment.prepare_qt_environment(environ)


def check_xcb_runtime(environ: MutableMapping[str, str] | None = None) -> None:
    """Fail before Qt aborts when the XCB cursor runtime is unavailable."""
    global _XCB_RUNTIME_HANDLE
    _gui_environment.check_xcb_runtime(environ)
    _XCB_RUNTIME_HANDLE = _gui_environment._XCB_RUNTIME_HANDLE


def _import_qt():
    try:
        from PyQt5.QtCore import QT_VERSION_STR
        from PyQt5.QtGui import QColor, QPalette
        from PyQt5.QtWidgets import QApplication
    except ImportError as exc:
        raise RuntimeError(
            f"PyQt5 is unavailable in {sys.executable}. Install PyQt5 for the "
            "selected Python 3.9 runtime."
        ) from exc
    return QApplication, QColor, QPalette, QT_VERSION_STR


def apply_application_style(application) -> None:
    """家族外观：亮底 + 红棕强调，密集表格沿用同一套样式表。"""
    application.setStyle("Fusion")
    apply_family_style(application)


def run_gui(
    *,
    source: str | None = None,
    cache_dir: str | None = None,
    force: bool = False,
    bridge: bool = False,
) -> int:
    parent_identity = capture_parent_identity()
    prepare_qt_environment()
    check_xcb_runtime()
    QApplication, _QColor, _QPalette, _qt_version = _import_qt()
    from .main_window import DspfMainWindow

    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("RCE DSPF Analyzer")
    application.setOrganizationName("RCE")
    apply_application_style(application)
    window = DspfMainWindow(cache_dir=cache_dir, bridge_enabled=bridge)
    window.show()
    _parent_monitor = install_parent_monitor(application, parent_identity, window.close)
    if not window.isVisible():
        return _finalize_gui_exit(0, parent_identity)
    if source:
        window.open_path(source, force=force)
    status = application.exec_()
    return _finalize_gui_exit(status, parent_identity)


__all__ = [
    "capture_parent_identity",
    "check_xcb_runtime",
    "install_parent_monitor",
    "prepare_qt_environment",
    "run_gui",
]
