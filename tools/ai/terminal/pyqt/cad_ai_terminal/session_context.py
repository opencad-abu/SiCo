"""Qt-native context actions and deferred popup operations for one terminal."""

from __future__ import annotations

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtWidgets import QAction, QApplication


class TerminalContextMenu:
    def __init__(
        self,
        parent,
        terminal,
        *,
        new_session,
        copy,
        paste,
        select_all,
        find,
        is_closing,
    ):
        self._is_closing = is_closing
        self._terminal = terminal
        self._context_operation = None
        self._context_close_timer = QTimer(parent)
        self._context_close_timer.setSingleShot(True)
        self._context_close_timer.setInterval(0)
        self._context_close_timer.timeout.connect(self._retry_context_operation)
        self._install(new_session, copy, paste, select_all, find)

    def set_new_session_enabled(self, enabled):
        self._new_session_action.setEnabled(enabled)

    def set_paste_enabled(self, enabled):
        self._paste_action.setEnabled(enabled)

    def stop(self):
        self._context_operation = None
        self._context_close_timer.stop()

    def release(self):
        self.stop()
        self._terminal = None
        self._context_surface = None
        self._context_actions = ()

    def _install(
        self,
        new_session_callback,
        copy_callback,
        paste_callback,
        select_callback,
        find_callback,
    ) -> None:
        terminal = self._terminal
        surface = terminal.focusProxy() or terminal
        new_session = QAction(QIcon.fromTheme("tab-new"), "New Session", surface)
        new_session.setObjectName("newSessionAction")
        new_session.setShortcut(QKeySequence("Ctrl+Shift+T"))
        new_session.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        first_separator = QAction(surface)
        first_separator.setSeparator(True)
        copy = QAction(QIcon.fromTheme("edit-copy"), "Copy", surface)
        copy.setObjectName("contextCopyAction")
        paste = QAction(QIcon.fromTheme("edit-paste"), "Paste", surface)
        paste.setObjectName("contextPasteAction")
        select_all = QAction(QIcon.fromTheme("edit-select-all"), "Select All", surface)
        select_all.setObjectName("selectAllAction")
        select_all.setShortcut(QKeySequence("Ctrl+Shift+A"))
        select_all.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        second_separator = QAction(surface)
        second_separator.setSeparator(True)
        find = QAction(QIcon.fromTheme("edit-find"), "Find", surface)
        find.setObjectName("contextFindAction")
        actions = (
            new_session,
            first_separator,
            copy,
            paste,
            select_all,
            second_separator,
            find,
        )
        surface.addActions(actions)
        # Keep right-click dispatch in Qt for production SIP 12.11.
        surface.setContextMenuPolicy(Qt.ActionsContextMenu)
        copy.setEnabled(True)
        new_session.triggered.connect(lambda _checked=False: new_session_callback())
        copy.triggered.connect(lambda _checked=False: copy_callback())
        paste.triggered.connect(lambda _checked=False: paste_callback())
        select_all.triggered.connect(lambda _checked=False: select_callback())
        find.triggered.connect(lambda _checked=False: find_callback())
        self._context_surface = surface
        self._context_actions = actions
        self._new_session_action = new_session
        self._paste_action = paste

    def defer_context_operation(self, operation) -> bool:
        popup = self._active_context_popup()
        if popup is None and self._context_operation is None:
            return False
        self._context_operation = operation
        if popup is not None:
            popup.close()
        self._context_close_timer.start()
        return True

    def _active_context_popup(self):
        popup = QApplication.activePopupWidget()
        if popup is None:
            return None
        try:
            popup_actions = popup.actions()
            return (
                popup
                if any(action in popup_actions for action in self._context_actions)
                else None
            )
        except RuntimeError:
            return None

    def _retry_context_operation(self) -> None:
        if self._context_operation is None:
            return
        popup = self._active_context_popup()
        if popup is not None:
            popup.close()
            self._context_close_timer.start()
            return
        QTimer.singleShot(0, self._run_context_operation)

    def _run_context_operation(self) -> None:
        operation = self._context_operation
        self._context_operation = None
        if operation is not None and not self._is_closing():
            operation()
