"""Per-tab terminal lifetime, shell state and callback ownership.

The factory keeps Qt optional until run_frontend has validated its runtime.
Input helpers and shell sanitation are legacy same-object exports; remove them
when supported callers have migrated to input_behavior and shell_environment.
"""

from __future__ import annotations

from cadgui import theme
from sicoenv import read as environment_setting

import os
import signal
from pathlib import Path
from .input_behavior import (
    INPUT_METHOD_CURSOR_SYNC_MS,
    _refresh_input_method_cursor,
    apply_scroll_policy,
)
from .shell_environment import sanitize_shell_environment
from .terminal_widget import create_terminal_widget


def create_terminal_session_class(
    *,
    qt,
    qtermwidget_type,
    options,
    resource_path,
    append_test_output,
    fixed_pitch_terminal_font,
    schedule_scroll_restore,
):
    """Build the QWidget subclass after the optional Qt runtime is available."""
    QApplication = qt["QApplication"]
    QTimer = qt["QTimer"]
    QVBoxLayout = qt["QVBoxLayout"]
    QWidget = qt["QWidget"]
    Qt = qt["Qt"]
    from .session_context import TerminalContextMenu

    class TerminalSession(QWidget):
        def __init__(
            self,
            number: int,
            new_session_callback,
            finished_callback,
            status_callback,
            parent,
        ) -> None:
            super().__init__(parent)
            self.number = number
            self.title = f"Session {number}"
            self._terminal = None
            self._context = None
            self._finished_callback = finished_callback
            self._status_callback = status_callback
            self._finished = False
            self._closing = False
            self._status_text = "Starting"
            self._status_color = "#9a6700"
            self._monitor = QTimer(self)
            self._monitor.setInterval(250)
            self._monitor.timeout.connect(self._poll_session)
            self._input_method_cursor_timer = QTimer(self)
            self._input_method_cursor_timer.setSingleShot(True)
            self._input_method_cursor_timer.setInterval(INPUT_METHOD_CURSOR_SYNC_MS)
            self._input_method_cursor_timer.timeout.connect(
                self._refresh_input_method_cursor
            )

            self.setObjectName("terminalSession")
            self.setProperty("sessionNumber", number)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            self._start_terminal(layout, new_session_callback)

        @property
        def terminal(self):
            return self._terminal

        @property
        def status(self) -> tuple[str, str]:
            return self._status_text, self._status_color

        @property
        def finished(self) -> bool:
            return self._finished

        def is_running(self) -> bool:
            if self._terminal is None or self._finished or self._closing:
                return False
            try:
                return self._terminal.getShellPID() > 0
            except RuntimeError:
                return False

        def shell_pid(self) -> int:
            if not self.is_running():
                return 0
            try:
                return self._terminal.getShellPID()
            except RuntimeError:
                return 0

        def invoke(self, method: str) -> None:
            terminal = self._terminal
            if terminal is None:
                return
            try:
                getattr(terminal, method)()
            except RuntimeError:
                pass

        def set_new_session_enabled(self, enabled: bool) -> None:
            if self._context is not None:
                self._context.set_new_session_enabled(enabled)

        def set_paste_enabled(self, enabled: bool) -> None:
            if self._context is not None:
                self._context.set_paste_enabled(enabled)

        def focus_terminal(self) -> None:
            if self._terminal is not None:
                self._terminal.setFocus(Qt.OtherFocusReason)
                self._queue_input_method_cursor_update()

        def stop_monitor(self) -> None:
            self._monitor.stop()

        def begin_shutdown(self) -> None:
            """Stop callbacks and ask this tab's agent to exit without blocking Qt."""
            self._closing = True
            self._monitor.stop()
            if self._context is not None:
                self._context.stop()
            self._input_method_cursor_timer.stop()
            self._signal_process(signal.SIGHUP)

        def force_shutdown(self) -> None:
            """Force a tab that ignored the graceful window-wide shutdown."""
            self._signal_process(signal.SIGKILL)

        def copy_selection(self) -> None:
            self.invoke("copyClipboard")

        def paste_clipboard(self) -> None:
            terminal = self._terminal
            if terminal is None:
                return
            terminal.pasteClipboard()
            terminal.scrollToEnd()
            terminal.setFocus(Qt.OtherFocusReason)

        def select_all(self) -> None:
            terminal = self._terminal
            if terminal is None or terminal.screenColumnsCount() <= 0:
                return
            screen_lines = getattr(terminal, "screenLinesCount", lambda: 1)()
            last_row = terminal.historyLinesCount() + max(1, screen_lines) - 1
            terminal.setSelectionStart(0, 0)
            terminal.setSelectionEnd(last_row, terminal.screenColumnsCount() - 1)
            terminal.setFocus(Qt.OtherFocusReason)

        def interrupt(self) -> None:
            if self._terminal is not None:
                self._terminal.sendText("\x03")

        def defer_context_operation(self, operation) -> bool:
            return bool(
                self._context and self._context.defer_context_operation(operation)
            )

        def release(self) -> None:
            self._closing = True
            self._finished = True
            self._monitor.stop()
            if self._context is not None:
                self._context.stop()
            self._input_method_cursor_timer.stop()
            self._release_terminal()

        def _start_terminal(self, layout, new_session_callback) -> None:
            terminal = create_terminal_widget(
                self,
                self.number,
                layout,
                qt=qt,
                qtermwidget_type=qtermwidget_type,
                options=options,
                resource_path=resource_path,
                fixed_pitch_terminal_font=fixed_pitch_terminal_font,
            )
            self._terminal = terminal
            self._context = TerminalContextMenu(
                self,
                terminal,
                new_session=new_session_callback,
                copy=self.copy_selection,
                paste=self.paste_clipboard,
                select_all=self.select_all,
                find=self.toggle_search,
                is_closing=lambda: self._closing,
            )
            terminal.finished.connect(self._finish_session)
            terminal.termKeyPressed.connect(self._apply_scroll_policy)
            terminal.termKeyPressed.connect(self._queue_input_method_cursor_update)
            terminal.receivedData.connect(self._queue_input_method_cursor_update)
            output_log = environment_setting(os.environ, "SICO_AI_TEST_OUTPUT", "").strip()
            if output_log:
                terminal.receivedData.connect(
                    lambda text: append_test_output(Path(output_log), text)
                )
            sanitize_shell_environment()
            terminal.startShellProgram()
            self._monitor.start()
            self._set_status("Running", theme.STATE_READY)
            self._queue_input_method_cursor_update()

        def _queue_input_method_cursor_update(self, *_arguments) -> None:
            if self._terminal is not None and not self._closing:
                self._input_method_cursor_timer.start()

        def _refresh_input_method_cursor(self) -> None:
            _refresh_input_method_cursor(QApplication, self._terminal, Qt)

        def toggle_search(self) -> None:
            self.invoke("toggleShowSearchBar")

        def _apply_scroll_policy(self, event) -> None:
            apply_scroll_policy(
                self._terminal,
                event,
                qt=Qt,
                scroll_bar_type=qt["QScrollBar"],
                timer_type=QTimer,
                schedule_scroll_restore=schedule_scroll_restore,
            )

        def _poll_session(self) -> None:
            terminal = self._terminal
            if terminal is None or self._finished or self._closing:
                return
            try:
                if terminal.getShellPID() <= 0:
                    self._finish_session()
            except RuntimeError:
                self._finish_session()

        def _finish_session(self) -> None:
            if self._finished or self._closing:
                return
            self._finished = True
            self._monitor.stop()
            self._input_method_cursor_timer.stop()
            self._set_status("Exited", theme.STATE_ERROR)
            self._finished_callback(self)

        def _set_status(self, text: str, color: str) -> None:
            self._status_text = text
            self._status_color = color
            self._status_callback(self)

        def _signal_process(self, signum: int) -> None:
            terminal = self._terminal
            if terminal is None:
                return
            try:
                process_id = terminal.getShellPID()
                if process_id > 0:
                    os.kill(process_id, signum)
            except (OSError, RuntimeError):
                pass

        def _release_terminal(self) -> None:
            terminal = self._terminal
            if terminal is None:
                return
            self._terminal = None
            if self._context is not None:
                self._context.release()
                self._context = None
            try:
                terminal.close()
                terminal.deleteLater()
            except RuntimeError:
                pass

    return TerminalSession


__all__ = [
    "INPUT_METHOD_CURSOR_SYNC_MS",
    "_refresh_input_method_cursor",
    "create_terminal_session_class",
]
