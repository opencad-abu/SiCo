"""Window and tab close decisions with popup-safe session cleanup."""

from __future__ import annotations

from cadgui.prompts import CANCEL, SiConfirm
from PyQt5.QtCore import QTimer
from .window_shutdown import WindowShutdown


class WindowCloseController:
    def __init__(
        self,
        parent,
        *,
        sessions,
        remove_session,
        release_sessions,
        close_window,
        minimize_window,
        sync_current,
        cancel_rename,
    ):
        self._parent = parent
        self._sessions = sessions
        self._remove_session = remove_session
        self._close = close_window
        self._minimize = minimize_window
        self._sync_current_session = sync_current
        self._cancel_session_rename = cancel_rename
        self._close_without_prompt = False
        self._close_dialog = None
        self._tab_close_dialog = None
        self._release_sessions = release_sessions
        self._shutdown = WindowShutdown(
            parent,
            sessions,
            release_sessions,
            self._finish_shutdown,
        )

    @property
    def closing(self):
        return self._close_without_prompt

    def stop(self):
        self._shutdown.stop()

    def close_empty(self):
        self._close_without_prompt = True
        self._close()

    def _finish_shutdown(self):
        self.close_empty()

    def request_close_session(self, session, *, confirm: bool) -> None:
        if session not in self._sessions():
            return
        if session.defer_context_operation(
            lambda: self.request_close_session(session, confirm=confirm)
        ):
            return
        if confirm and session.is_running() and not self._close_without_prompt:
            dialog = SiConfirm("AI Assistant Session",
                               f"{session.title} is still running.", self._parent)
            dialog.setObjectName("tabCloseDialog")
            dialog.add_choice("exit", "Exit Session", danger=True)
            dialog.add_choice(CANCEL, CANCEL, default=True, escape=True)
            self._tab_close_dialog = (dialog, session)
            choice = dialog.ask()
            self._tab_close_dialog = None
            if self._close_without_prompt:
                self._close()
                return
            if session not in self._sessions():
                return
            if session.is_running() and choice != "exit":
                return
        self._remove_session(session)

    def session_finished(self, session) -> None:
        if session not in self._sessions():
            return
        if self._tab_close_dialog is not None:
            dialog, target = self._tab_close_dialog
            if target is session:
                dialog.reject()
                return
        if self._close_dialog is not None:
            if not any(item.is_running() for item in self._sessions()):
                self._close_without_prompt = True
                self._close_dialog.reject()
            return
        # Auto-close happens asynchronously after QTermWidget emits
        # finished. Keep the final tab so a fast launcher failure and its
        # exit text remain visible instead of making the window flash and
        # disappear. Completed tabs are still pruned automatically when
        # another independent session remains in the window.
        session.stop_monitor()
        if len(self._sessions()) == 1:
            self._sync_current_session()
            return
        QTimer.singleShot(0, lambda: self.request_close_session(session, confirm=False))

    def shutdown_from_owner(self) -> None:
        self._close_without_prompt = True
        self._cancel_session_rename(restore_focus=False)
        if self._close_dialog is not None:
            self._close_dialog.reject()
        elif self._tab_close_dialog is not None:
            self._tab_close_dialog[0].reject()
        else:
            # Keep owner shutdown on the same closeEvent path so an active
            # Qt-native context menu can unwind before its terminal dies.
            self._close()

    def handle_close(self, event) -> None:
        self._cancel_session_rename(restore_focus=False)
        if self._shutdown.active:
            event.ignore()
            return
        for session in tuple(self._sessions()):
            if session.defer_context_operation(self._close):
                event.ignore()
                return
        running = [session for session in self._sessions() if session.is_running()]
        if not running:
            self._release_sessions()
            event.accept()
            return
        if self._close_without_prompt:
            event.ignore()
            self._shutdown.begin()
            return
        noun = "session is" if len(running) == 1 else "sessions are"
        dialog = SiConfirm("AI Assistant Sessions",
                           f"{len(running)} AI Assistant {noun} still running.", self._parent)
        dialog.setObjectName("sessionCloseDialog")
        dialog.add_choice("exit", "Exit Sessions", danger=True)
        dialog.add_choice("minimize", "Minimize")
        dialog.add_choice(CANCEL, CANCEL, default=True, escape=True)
        self._close_dialog = dialog
        choice = dialog.ask()
        self._close_dialog = None
        if self._close_without_prompt:
            event.ignore()
            self._shutdown.begin()
        elif choice == "exit":
            event.ignore()
            self._shutdown.begin()
        else:
            event.ignore()
            if choice == "minimize":
                self._minimize()
            QTimer.singleShot(0, self._prune_finished_sessions)

    def _prune_finished_sessions(self) -> None:
        for session in tuple(self._sessions()):
            if session.finished:
                self._remove_session(session)
