"""PyQt5 application bootstrap for SiCo::MTS Netlistor."""

from __future__ import annotations

from pathlib import Path
from sicoresources import icon as resource_icon
import sys

from cadgui.branding import logo_text
from cadgui.chrome import apply_family_style
from cadgui.environment import check_xcb_runtime, prepare_qt_environment
from cadgui.lifecycle import finalize_gui_exit, install_parent_monitor
from cadgui.prompts import notice

from ..environment import SessionDescriptor
from ..errors import MtsNetlistorError, RequestValidationError
from ..project import module_roots
from .lifecycle import managed_gui_shutdown


def _import_qt():
    try:
        from PyQt5.QtGui import QIcon
        from PyQt5.QtWidgets import QApplication
    except ImportError as exc:
        raise MtsNetlistorError(
            f"PyQt5 is unavailable in {sys.executable}; "
            f"{logo_text()}::MTS Netlistor requires a PyQt5-enabled runtime"
        ) from exc
    return QApplication, QIcon


def _logo_path(environ=None) -> Path | None:
    return resource_icon("brand", "logo.png", environ)


def run_gui(
    *,
    session: SessionDescriptor | None = None,
    parent_identity: tuple[int, str] | None = None,
    session_path: str | Path | None = None,
    module_root: str | Path | None = None,
) -> int:
    """Start the GUI; Qt is imported only when this function is called."""

    prepare_qt_environment()
    check_xcb_runtime()
    QApplication, QIcon = _import_qt()
    from .main_window import MtsMainWindow

    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName(f"{logo_text()}::MTS Netlistor")
    application.setOrganizationName(logo_text())
    application.setStyle("Fusion")
    apply_family_style(application)
    logo = _logo_path()
    icon = None
    if logo is not None:
        icon = QIcon(str(logo))
        application.setWindowIcon(icon)
    try:
        configured_root = module_roots(module_root)[0]
    except RequestValidationError as exc:
        notice(
            None,
            "MTS project module directory",
            f"{exc}\n\nSet MTS_NETLISTOR_MODULEFILES to the project modulefile "
            "directory before starting MTS, or pass --module-root DIR.",
        )
        status = 1
    else:
        window = MtsMainWindow(session=session, module_root=configured_root)
        if icon is not None:
            window.setWindowIcon(icon)
        window.show()
        if parent_identity is not None:
            window._parent_monitor = install_parent_monitor(
                application, parent_identity, window.close
            )
        # Drain workers before the owner-bound hard exit path.
        with managed_gui_shutdown(application, window):
            status = application.exec_()
    # The launcher writes a private descriptor for this one GUI instance.
    # Remove it after Qt has stopped using the session snapshot, before the
    # owner-bound hard exit path is entered.
    if session_path is not None:
        try:
            Path(session_path).expanduser().resolve().unlink(missing_ok=True)
        except OSError:
            pass
    return finalize_gui_exit(status, parent_identity)


__all__ = ["apply_family_style", "run_gui"]
