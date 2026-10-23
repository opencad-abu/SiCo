"""Auxiliary user-launched terminal window built on QTermWidget.

This window is not the session command terminal. SiCo/Codex shell and EDA
commands use the session-owned command terminal; this auxiliary window only
opens an interactive local shell for the user. Compatibility exports refer to
the service's sole environment policy.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Mapping

from sicoenv import value
from sicoresources import current, terminal_data, terminal_resource

from PyQt5.QtCore import Qt, QTextCodec
from PyQt5.QtGui import QFontDatabase, QFontInfo, QKeySequence
from PyQt5.QtWidgets import QAction, QWidget

from sico import PRODUCT_NAME
from sico.service.terminal_platform import (
    PLATFORMS as PLATFORMS,
)
from sico.service.terminal_platform import (
    platform_name as platform_name,
)
from sico.service.terminal_startup import prepare_shell
# Legacy same-object exports; migrate callers to the service owner before removal.
from sico.service.terminal_environment import (
    LOGIN_SHELLS as LOGIN_SHELLS, SHELL_ENV_DROP as SHELL_ENV_DROP,
    shell_command as shell_command,
)

from .appearance import AppearancePreferences
from .chrome import SiWidget
from .si_prompt import notice


COLOR_SCHEMES = {
    "light": "color-schemes/SiCo.colorscheme",
    "dark": "color-schemes/SiCoDark.colorscheme",
}
QTERMWIDGET_BINDING = Path("python") / "QTermWidget.abi3.so"
QTERMWIDGET_LIBRARY = Path("lib") / "libqtermwidget5.so.1"


def qtermwidget_runtime(
    environment: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> Path | None:
    """Use the selected installation's QTermWidget pair for this platform."""

    source = os.environ if environment is None else environment
    platform = platform or platform_name(source)
    if platform is None:
        return None
    if platform not in PLATFORMS:
        return None
    installed = current(source)
    runtime = installed.path("tools/ai/runtime/" + platform)
    configured = value(source, "SICO_AI_QTERMWIDGET_RUNTIME", ("CAD_AI_QTERMWIDGET_RUNTIME",))
    if configured is not None:
        path = Path(configured)
        if not configured.strip() or not path.is_absolute() or path.resolve() != runtime:
            raise ValueError("SICO_AI_QTERMWIDGET_RUNTIME conflicts with the installation")
    for relative in (QTERMWIDGET_BINDING, QTERMWIDGET_LIBRARY):
        path = installed.path("tools/ai/runtime/" + platform + "/" + relative.as_posix())
        if not path.is_file():
            return None
    return runtime


def load_qtermwidget(runtime: Path):
    """Import the QTermWidget class from the selected runtime directory."""

    data = str(terminal_data())
    os.environ["SICO_AI_QTERMWIDGET_DATA_PATH"] = data
    # QTermWidget now consumes the current setting only.
    os.environ.pop("CAD_AI_QTERMWIDGET_DATA_PATH", None)
    library = runtime / QTERMWIDGET_LIBRARY
    try:
        # Preload so the extension also resolves when the process did not
        # start with the runtime directory in LD_LIBRARY_PATH.
        ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL)
    except OSError:
        pass  # the binding's $ORIGIN/../lib RPATH still resolves it
    directory = str(runtime / "python")
    if directory not in sys.path:
        sys.path.insert(0, directory)
    from QTermWidget import QTermWidget  # noqa: PLC0415

    return QTermWidget


def fixed_pitch_terminal_font(
    database=QFontDatabase,
    info=QFontInfo,
    *,
    allow_bundled=True,
    point_size=None,
):
    """A font Qt resolves to fixed pitch, preferring the common mono families."""

    if allow_bundled:
        from cadgui.fonts import terminal_font

        bundled = terminal_font(point_size=point_size)
        if bundled is not None:
            return bundled
    font = database.systemFont(database.FixedFont)
    if not info(font).fixedPitch():
        instance = database()
        families = instance.families()
        for candidate in (
            "DejaVu Sans Mono",
            "Liberation Mono",
            "Nimbus Mono PS",
            "Courier New",
            "Monospace",
        ):
            if candidate in families and instance.isFixedPitch(candidate):
                font.setFamily(candidate)
                font.setStyleHint(font.Monospace)
                font.setFixedPitch(True)
                break
    if point_size is not None:
        font.setPointSize(point_size)
    return font


def apply_terminal_appearance(terminal, preferences, resource=None):
    """Apply the active SiCo palette and body-text size to one QTermWidget."""

    terminal.setTerminalFont(
        fixed_pitch_terminal_font(point_size=preferences.text_point_size))
    relative = COLOR_SCHEMES.get(preferences.theme)
    resolver = terminal_resource if resource is None else resource
    scheme = resolver(relative) if relative is not None else None
    if scheme:
        terminal.setColorScheme(str(scheme))


class SiTerminalWindow(SiWidget):
    """QTermWidget terminal with the shared SiCo chrome and palette."""

    # SiCo 摆位：工具窗口放右半屏，主窗口占左半屏。
    launch_half = "right"

    def __init__(self, qtermwidget_type, parent=None, workspace=None, command=None,
                 environment=None, appearance=None):
        source = dict(os.environ if environment is None else environment)
        command, child_environment = prepare_shell(source, command=command, inherited=os.environ)
        self._appearance = appearance or AppearancePreferences()
        super().__init__(title=f"{PRODUCT_NAME}::终端", parent=parent)
        self.setObjectName("sicoTerminalWindow")
        terminal = qtermwidget_type(0, self)
        self.terminal = terminal
        terminal.setObjectName("sicoTerminal")
        terminal.setShellProgram(command[0])
        terminal.setArgs(command[1:])
        terminal.setEnvironment([name + "=" + value for name, value in child_environment.items()])
        if workspace is not None:
            terminal.setWorkingDirectory(str(workspace))
        terminal.setHistorySize(5000)
        terminal.setScrollBarPosition(qtermwidget_type.ScrollBarRight)
        terminal.setTextCodec(QTextCodec.codecForName(b"UTF-8"))
        apply_terminal_appearance(terminal, self._appearance)
        self.content_layout().addWidget(terminal, 1)
        self._install_context_menu(terminal)
        self.resize(920, 600)
        terminal.startShellProgram()

    def refresh_appearance(self, preferences=None):
        """Keep the terminal palette and font on the active SiCo appearance."""
        super().refresh_appearance(preferences)
        if preferences is not None:
            self._appearance = preferences
        apply_terminal_appearance(self.terminal, self._appearance)

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_placed", False):
            self._placed = True
            self.place_on_launch_half(self.parentWidget())

    def _install_context_menu(self, terminal) -> None:
        surface = terminal.focusProxy() or terminal
        copy = QAction("复制", surface)
        copy.setShortcut(QKeySequence("Ctrl+Shift+C"))
        copy.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        copy.triggered.connect(terminal.copyClipboard)
        paste = QAction("粘贴", surface)
        paste.setShortcut(QKeySequence("Ctrl+Shift+V"))
        paste.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        paste.triggered.connect(self._paste)
        select_all = QAction("全选", surface)
        select_all.setShortcut(QKeySequence("Ctrl+Shift+A"))
        select_all.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        select_all.triggered.connect(self._select_all)
        surface.addActions([copy, paste, select_all])
        # Keep right-click dispatch in Qt for production SIP 12.11.
        surface.setContextMenuPolicy(Qt.ActionsContextMenu)

    def _paste(self, _checked=False) -> None:
        self.terminal.pasteClipboard()
        self.terminal.scrollToEnd()
        self.terminal.setFocus(Qt.OtherFocusReason)

    def _select_all(self, _checked=False) -> None:
        terminal = self.terminal
        if terminal.screenColumnsCount() <= 0:
            return
        screen_lines = getattr(terminal, "screenLinesCount", lambda: 1)()
        last_row = terminal.historyLinesCount() + max(1, screen_lines) - 1
        terminal.setSelectionStart(0, 0)
        terminal.setSelectionEnd(last_row, terminal.screenColumnsCount() - 1)
        terminal.setFocus(Qt.OtherFocusReason)


def launch_directory(window: QWidget | None = None) -> Path:
    """Return the project directory the terminal opens in.

    The session journal uses the selected project's ``ai/agent`` directory and is the same
    directory the controller uses for the session, so the tool follows the
    project the Virtuoso instance was started from.
    """

    api = getattr(window, "api", None)
    if api is not None:
        return api.launch_directory
    return Path.cwd()


def report_unavailable(window: QWidget | None, message: str) -> None:
    """Show why the terminal tool could not open, in the SiCo window style."""

    notice(window, "终端", message)


def _forget_terminal(window) -> None:
    try:
        window._sico_terminal_window = None
    except (AttributeError, RuntimeError):
        pass


def open_terminal(window: QWidget | None) -> None:
    """Open (or raise) the auxiliary interactive terminal window."""

    existing = getattr(window, "_sico_terminal_window", None) if window is not None else None
    if isinstance(existing, SiTerminalWindow):
        existing.show()
        existing.raise_()
        existing.activateWindow()
        return
    try:
        runtime = qtermwidget_runtime()
    except ValueError as exc:
        report_unavailable(window, str(exc))
        return
    if runtime is None:
        report_unavailable(
            window,
            "未找到 QTermWidget 运行时。\n"
            "请确认当前 SiCo 安装包含适用平台的终端组件。",
        )
        return
    try:
        qtermwidget_type = load_qtermwidget(runtime)
    except (ImportError, OSError, ValueError) as exc:
        report_unavailable(window, f"无法加载 QTermWidget：{exc}")
        return
    try:
        terminal_window = SiTerminalWindow(
            qtermwidget_type,
            parent=window,
            workspace=launch_directory(window),
            appearance=getattr(window, "appearance", None),
        )
    except ValueError as exc:
        report_unavailable(window, f"无法准备终端环境：{exc}")
        return
    terminal_window.setAttribute(Qt.WA_DeleteOnClose, True)
    if window is not None:
        window._sico_terminal_window = terminal_window
        terminal_window.destroyed.connect(lambda *_: _forget_terminal(window))
    terminal_window.show()
