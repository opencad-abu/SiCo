"""Compose independent terminal tabs and route actions to the selected session."""

from __future__ import annotations

from cadgui import theme
from cadgui.chrome import SiMainWindow
from cadgui.prompts import notice
from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QTabWidget
from .appearance import _logo_text
from .resources import _logo_candidates
from .tab_renamer import TabRenamer
from .toolbar import TerminalToolbar
from .window_close import WindowCloseController


def logo_mark() -> QIcon:
    """站点 logo 作标题栏图标与窗口图标；找不到就留空。"""

    for candidate in _logo_candidates():
        if candidate.is_file():
            icon = QIcon(str(candidate))
            if not icon.isNull():
                QApplication.setWindowIcon(icon)
                return icon
    return QIcon()


class TerminalWindow(SiMainWindow):
    MAXIMUM_SESSIONS = 8

    def __init__(self, options, session_type):
        super().__init__(f"{_logo_text()}::AI Assistant@{options.workspace}", mark=logo_mark())
        # 终端窗口没有菜单栏：标题栏下面直接是工具条。
        self.menu_bar.hide()
        self._session_type = session_type
        self._sessions = []
        self._next_session_number = 1
        self.setObjectName("cadAiTerminalWindow")
        self.resize(1120, 760)
        self.setMinimumSize(720, 480)
        self._toolbar = TerminalToolbar(
            self,
            options.workspace,
            copy=self._copy_selection,
            paste=self._paste_clipboard,
            find=self._toggle_search,
            zoom_out_callback=self._zoom_out,
            zoom_in_callback=self._zoom_in,
            interrupt_callback=self._interrupt_session,
        )
        self.addToolBar(self._toolbar)
        QApplication.clipboard().dataChanged.connect(self._update_paste_action)
        self._tabs = QTabWidget(self)
        self._tabs.setObjectName("terminalTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(True)
        self._tabs.tabBar().setObjectName("sessionTabBar")
        self._renamer = TabRenamer(
            self._tabs,
            self._current_session,
            self._queue_current_session_focus,
        )
        self._closing = WindowCloseController(
            self,
            sessions=lambda: tuple(self._sessions),
            remove_session=self._remove_session,
            release_sessions=self._release_sessions,
            close_window=self.close,
            minimize_window=self.showMinimized,
            sync_current=self._sync_current_session,
            cancel_rename=self._renamer.cancel,
        )
        self._tabs.currentChanged.connect(self._current_tab_changed)
        self._tabs.tabCloseRequested.connect(self._tab_close_requested)
        self.setCentralWidget(self._tabs)
        self._add_session(initial=True)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        session = self._current_session()
        if session is not None:
            QTimer.singleShot(0, session.focus_terminal)

    def _with_terminal(self, method: str) -> None:
        session = self._current_session()
        if session is not None:
            session.invoke(method)

    def _copy_selection(self) -> None:
        self._with_terminal("copyClipboard")

    def _toggle_search(self) -> None:
        self._with_terminal("toggleShowSearchBar")

    def _zoom_out(self) -> None:
        self._with_terminal("zoomOut")

    def _zoom_in(self) -> None:
        self._with_terminal("zoomIn")

    def _update_paste_action(self) -> None:
        mime_data = QApplication.clipboard().mimeData()
        enabled = bool(mime_data and mime_data.hasText())
        self._toolbar.set_paste_enabled(enabled and self._current_session() is not None)
        for session in self._sessions:
            session.set_paste_enabled(enabled)

    def _paste_clipboard(self) -> None:
        session = self._current_session()
        if session is not None:
            session.paste_clipboard()

    def _interrupt_session(self) -> None:
        session = self._current_session()
        if session is not None:
            session.interrupt()

    @property
    def _terminal(self):
        session = self._current_session()
        return session.terminal if session is not None else None

    def _current_session(self):
        widget = self._tabs.currentWidget() if hasattr(self, "_tabs") else None
        return widget if isinstance(widget, self._session_type) else None

    def _add_session(self, *, initial: bool = False) -> None:
        if self._closing.closing or (not self.isVisible() and not initial):
            return
        if len(self._sessions) >= self.MAXIMUM_SESSIONS:
            if not initial:
                notice(self, "AI Assistant Sessions",
                       f"At most {self.MAXIMUM_SESSIONS} sessions can run in one window.")
            return
        number = self._next_session_number
        self._next_session_number += 1
        try:
            session = self._session_type(
                number,
                self._queue_new_session,
                self._closing.session_finished,
                self._session_status_changed,
                self._tabs,
            )
        except Exception as exc:
            if initial:
                raise
            notice(self, "AI Assistant Session",
                   f"The new AI Assistant session could not be started:\n{exc}")
            return
        self._sessions.append(session)
        index = self._tabs.addTab(session, session.title)
        self._tabs.setTabToolTip(index, session.title)
        self._tabs.setCurrentIndex(index)
        self._update_session_actions()
        self._sync_current_session()
        session.focus_terminal()

    def _queue_new_session(self, *_arguments) -> None:
        current = self._current_session()
        if current is None:
            return
        if not current.defer_context_operation(self._add_session_from_menu):
            QTimer.singleShot(0, self._add_session_from_menu)

    def _add_session_from_menu(self) -> None:
        self._add_session(initial=False)

    def _queue_current_session_focus(self) -> None:
        def focus_current_session() -> None:
            session = self._current_session()
            if session is not None:
                session.focus_terminal()

        QTimer.singleShot(0, focus_current_session)

    def _tab_close_requested(self, index: int) -> None:
        widget = self._tabs.widget(index)
        if isinstance(widget, self._session_type):
            self._closing.request_close_session(widget, confirm=True)

    def _remove_session(self, session) -> None:
        if session not in self._sessions:
            return
        self._renamer.removing(session)
        index = self._tabs.indexOf(session)
        if index >= 0:
            self._tabs.removeTab(index)
        self._sessions.remove(session)
        session.release()
        session.deleteLater()
        if not self._sessions:
            self._closing.close_empty()
            return
        self._update_session_actions()
        self._sync_current_session()

    def _finish_session(self) -> None:
        session = self._current_session()
        if session is not None:
            session._finish_session()

    def _session_status_changed(self, session) -> None:
        if session is self._current_session():
            self._toolbar.set_status(*session.status)

    def _current_tab_changed(self, _index: int) -> None:
        self._renamer.tab_changed()
        self._sync_current_session()
        session = self._current_session()
        if session is not None:
            session.focus_terminal()

    def _sync_current_session(self) -> None:
        session = self._current_session()
        enabled = session is not None and session.terminal is not None
        self._toolbar.set_session_enabled(
            enabled, bool(session and session.is_running())
        )
        if session is None:
            self._toolbar.set_status("Exited", theme.STATE_ERROR)
        else:
            self._toolbar.set_status(*session.status)
        self._update_paste_action()

    def _update_session_actions(self) -> None:
        enabled = len(self._sessions) < self.MAXIMUM_SESSIONS
        for session in self._sessions:
            session.set_new_session_enabled(enabled)

    def show_from_owner(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _release_sessions(self) -> None:
        self._closing.stop()
        sessions = tuple(self._sessions)
        self._sessions.clear()
        for session in sessions:
            index = self._tabs.indexOf(session)
            if index >= 0:
                self._tabs.removeTab(index)
            session.release()
            session.deleteLater()

    def shutdown_from_owner(self):
        self._closing.shutdown_from_owner()

    def closeEvent(self, event):
        self._closing.handle_close(event)
