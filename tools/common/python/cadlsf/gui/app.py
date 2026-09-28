"""PyQt5 bootstrap for the standalone and Virtuoso-owned LSF Monitor."""

from __future__ import annotations

import os
from pathlib import Path
from sicoresources import icon as resource_icon
import sys

from cadgui.branding import logo_text
from cadgui.chrome import apply_family_style
from cadgui.environment import check_xcb_runtime, prepare_qt_environment
from cadgui.lifecycle import (
    capture_parent_identity,
    finalize_gui_exit,
    install_parent_monitor,
)

from ..cache import MonitorTopologyCache, default_monitor_cache_root
from ..collector import CollectorConfig, current_os_user


def lsf_environment_identity(environ=None) -> tuple[str, ...]:
    environment = os.environ if environ is None else environ
    names = (
        "LSF_CLUSTER_NAME",
        "LSF_ENVDIR",
        "LSF_SERVERDIR",
        "LSB_SHAREDIR",
        "LSF_BINDIR",
        "LSF_LIBDIR",
        "LSF_VERSION",
    )
    return tuple(f"{name}={environment.get(name, '')}" for name in names)


def _import_qt():
    try:
        from PyQt5.QtGui import QColor, QIcon, QPalette
        from PyQt5.QtWidgets import QApplication
    except ImportError as exc:
        raise RuntimeError(
            f"PyQt5 is unavailable in {sys.executable}. LSF Load Monitor "
            "requires the configured Python 3.9 runtime with PyQt5 installed."
        ) from exc
    return QApplication, QColor, QIcon, QPalette


def _logo_path() -> Path | None:
    return resource_icon("brand", "logo.png")


def apply_application_style(application, QColor, QPalette) -> None:
    application.setStyle("Fusion")
    apply_family_style(application)


def run_gui(
    *,
    config: CollectorConfig | None = None,
    initial_queue: str | None = None,
    refresh_interval: int = 10,
    parent_pid: int | str | None = None,
    output: Path | None = None,
) -> int:
    parent_identity = capture_parent_identity(parent_pid)
    resolved_config = config or CollectorConfig()
    user = current_os_user()
    topology_cache = MonitorTopologyCache(
        default_monitor_cache_root(create=True),
        user=user,
        command_signature=(
            resolved_config.bqueues,
            resolved_config.bhosts,
            resolved_config.lsload,
            resolved_config.bjobs,
            resolved_config.lshosts,
        ),
        environment_identity=lsf_environment_identity(),
    )
    prepare_qt_environment()
    check_xcb_runtime()
    QApplication, QColor, QIcon, QPalette = _import_qt()
    from .main_window import LsfLoadMonitorWindow

    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("LSF Load Monitor")
    application.setOrganizationName(logo_text())
    apply_application_style(application, QColor, QPalette)
    logo = _logo_path()
    if logo is not None:
        application.setWindowIcon(QIcon(str(logo)))
    window = LsfLoadMonitorWindow(
        config=resolved_config,
        initial_queue=initial_queue,
        refresh_interval=refresh_interval,
        output=output,
        topology_cache=topology_cache,
    )
    window.show()
    parent_monitor = install_parent_monitor(
        application, parent_identity, window.close
    )
    window._parent_monitor = parent_monitor
    if not window.isVisible():
        return finalize_gui_exit(0, parent_identity)
    status = application.exec_()
    return finalize_gui_exit(status, parent_identity)


__all__ = ["apply_application_style", "lsf_environment_identity", "run_gui"]
