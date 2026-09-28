"""The one right-click menu of a session row and what each entry does."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMenu


class SessionRowMenu:
    """Session-row actions menu: continue, end, rename, delete — nothing else."""

    def _can_end(self):
        """A service window can end and archive an active session; legacy shapes cannot."""

        from .service_connection import service_ending

        return service_ending(self.window) is not None

    def _menu(self, position, tree=None):
        try:
            if tree is None:
                # 直接调用（测试/键盘）：按位置找到所在的那棵树。
                tree = self.current if self.current.itemAt(position) is not None else self.list
            item = tree.itemAt(position)
            if item is None:
                return
            parent_id = item.data(0, Qt.UserRole + 1)
            session_id = parent_id or item.data(0, Qt.UserRole)
            unavailable = item.data(0, Qt.UserRole + 2)
            if item.data(0, Qt.UserRole + 3):
                menu = QMenu(self)
                delete = menu.addAction("删除")
                if menu.exec_(tree.viewport().mapToGlobal(position)) is delete:
                    self._delete(session_id)
                return
            if unavailable:
                self._alert(self._unavailable_text(unavailable))
                return
            menu = QMenu(self)
            resume = rename = end = delete = None
            resume = menu.addAction("继续")
            # 服务窗口里活动会话只能“结束并归档”，只有归档记录才提供“删除”；
            # 没有结束路径的进程内形状（历史联调）保持原来的删除语义。
            live = self._live_session(session_id) is not None and self._can_end()
            end = self._end_entry(menu, session_id) if live else None
            rename = menu.addAction("重命名")
            delete = None if live else menu.addAction("删除")
            action = menu.exec_(tree.viewport().mapToGlobal(position))
            if action is None:
                return
            if action is resume:
                self._continue_session(session_id)
            elif end is not None and action is end:
                self._ask_end(session_id)
            elif rename is not None and action is rename:
                self._rename(session_id)
            elif delete is not None and action is delete:
                self._delete(session_id)
        except Exception as exc:
            self.window.info_app.error("会话操作失败", str(exc))
