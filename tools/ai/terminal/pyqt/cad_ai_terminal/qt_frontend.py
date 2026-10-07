"""Lazy Qt application bootstrap for the CAD AI terminal frontend.

Private helper aliases are legacy imports for existing development consumers.
Remove after callers migrate to appearance, input_behavior and resources.
"""

from __future__ import annotations

import os
import sys
from .protocol import LaunchOptions
from .control_channel import ControlChannel
from .terminal_session import create_terminal_session_class
from .appearance import (
    _logo_text,
    _normalize_session_title as _normalize_session_title,
    _fixed_pitch_terminal_font,
)
from .input_behavior import _schedule_scroll_restore
from .resources import (
    _resource_path,
    _logo_candidates as _logo_candidates,
)
from .output_capture import _append_test_output


def run_frontend(options: LaunchOptions) -> int:
    """Create the Qt application after the stdlib-only startup checks pass."""
    os.environ["QT_XCB_GL_INTEGRATION"] = "none"
    try:
        from PyQt5.QtCore import QSocketNotifier, Qt, QTextCodec, QTimer
        from PyQt5.QtGui import QFont, QFontDatabase, QFontInfo, QIcon, QKeySequence
        from PyQt5.QtWidgets import (
            QAction,
            QApplication,
            QScrollBar,
            QSizePolicy,
            QVBoxLayout,
            QWidget,
        )
        from QTermWidget import QTermWidget
        from .terminal_window import TerminalWindow
    except ImportError as exc:
        print(
            "sico-ai-terminal: PyQt5 and the QTermWidget 1.4 SIP binding "
            f"are required: {exc}",
            file=sys.stderr,
        )
        return 2

    TerminalSession = create_terminal_session_class(
        qt={
            "QAction": QAction,
            "QApplication": QApplication,
            "QFont": QFont,
            "QFontDatabase": QFontDatabase,
            "QFontInfo": QFontInfo,
            "QIcon": QIcon,
            "QKeySequence": QKeySequence,
            "QScrollBar": QScrollBar,
            "QSizePolicy": QSizePolicy,
            "QTextCodec": QTextCodec,
            "QTimer": QTimer,
            "QVBoxLayout": QVBoxLayout,
            "QWidget": QWidget,
            "Qt": Qt,
        },
        qtermwidget_type=QTermWidget,
        options=options,
        resource_path=_resource_path,
        append_test_output=_append_test_output,
        fixed_pitch_terminal_font=_fixed_pitch_terminal_font,
        schedule_scroll_restore=_schedule_scroll_restore,
    )

    application = QApplication(["sico-ai-terminal"])
    application.setApplicationName("sico-ai-terminal")
    application.setOrganizationName(_logo_text())
    try:
        window = TerminalWindow(options, TerminalSession)
    except RuntimeError as exc:
        print(f"sico-ai-terminal: {exc}", file=sys.stderr)
        return 2
    control = ControlChannel(
        application, window.show_from_owner, window.shutdown_from_owner, QSocketNotifier
    )
    application.aboutToQuit.connect(control.close)
    window.show()
    return application.exec_()
