"""会话与服务菜单的入口登记：关闭行固定最后，服务入口跟在“会话”右侧。"""

from __future__ import annotations

from PyQt5.QtWidgets import QAction, QMenu, QWidgetAction

from .workspace_closing import ClosingMenuEntry


class WorkspaceMenus:
    """Adds 会话/服务 menu entries to a window that already has these menus."""

    def _menu_action(self, title, callback, shortcut):
        action = QAction(title, self)
        if shortcut:
            action.setShortcut(shortcut)
        # Cython callables need an explicit adapter for QAction.triggered(bool).
        action.triggered.connect(lambda _checked=False: callback())
        return action

    def session_action(self, title, callback, shortcut=None):
        """One 会话 menu entry; the input area keeps the frequent actions."""
        action = self._menu_action(title, callback, shortcut)
        anchor = self._session_anchor()
        if anchor is not None:
            # Closing stays last and the service group stays below the plain entries.
            self.session_menu.insertAction(anchor, action)
        else:
            self.session_menu.addAction(action)
        return action

    def service_action(self, title, callback, shortcut=None):
        """One 服务 menu entry: the menu appears next to 会话 with the project service."""
        menu = getattr(self, "service_menu", None)
        if menu is None:
            menu = self.service_menu = QMenu("服务", self)
            self.menuBar().insertMenu(self.view_menu.menuAction(), menu)
        action = self._menu_action(title, callback, shortcut)
        menu.addAction(action)
        return action

    def _session_anchor(self):
        """Where the next 会话 entry goes: above the closing row."""
        return self._session_tail

    def session_tail_action(self, title, callback, shortcut=None):
        """Separated closing entry pinned to the bottom of the 会话 menu."""
        self._session_tail = self.session_menu.addSeparator()
        action = self._menu_action(title, callback, shortcut)
        if shortcut:
            # 关闭行是自绘控件，按键要挂在窗口上才生效。
            self.addAction(action)
        # One painted row: red, bold and carrying its own icon.
        entry = QWidgetAction(self.session_menu)
        entry.setText(title)
        entry.setDefaultWidget(ClosingMenuEntry(title, callback, parent=self.session_menu))
        entry.triggered.connect(lambda _checked=False: callback())
        self.session_menu.addAction(entry)
        return action
