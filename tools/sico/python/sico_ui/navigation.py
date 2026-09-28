"""Session navigation: single-click select, double-click review, plus the task queue."""

from __future__ import annotations

from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtGui import QBrush
from PyQt5.QtWidgets import (
    QLabel,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .navigation_actions import NavigationActions
from .passive_notices import PassiveNotices
from .navigation_menu import SessionRowMenu
from .navigation_status import ACTIVITY as ACTIVITY
from .navigation_status import (
    CHILD_ACTIVITY,
    STATUS_DOT,
    _session_time,
    activity_icon,
    activity_label,
    session_activity,
    unavailable_live_text,
)
from .tasks import TaskPanel
from .workspace import EvenTabWidget


def _deep_items(tree):
    """Every row of a session pane, child Codex threads included."""

    stack = [tree.topLevelItem(index) for index in range(tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        yield item
        stack.extend(item.child(index) for index in range(item.childCount()))


class Navigation(SessionRowMenu, NavigationActions, EvenTabWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.catalog_state = None
        self.catalog_version = -1
        self.catalog_snapshot = {"rows": (), "retained": {}}
        self.notices = PassiveNotices(window.info_app)
        # 单击选中的行（会话或子任务线程）；浏览/激活后会跟随到对应会话。
        self.selected_id = ""
        self._live_id = None
        self._catalog_blocked_runtime = None
        # 上块＝本窗口已打开的会话（绿/橙/蓝），下块＝未打开的历史记录（灰），只靠位置和圆点区分。
        self.current = self._session_tree("sessionCurrent")
        self.list = self._session_tree("sessionHistory")
        self.current_hint = self._hint("暂无已打开的会话")
        self.history_hint = self._hint("暂无历史会话")
        self.session_pane = self._session_pane()
        self._split_ready = False
        self.tasks = TaskPanel()
        self.addTab(self.session_pane, "会话历史")
        self.addTab(self.tasks, "任务队列")
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def _session_tree(self, object_name):
        """One session list; both panes share the selection and context menu."""

        tree = QTreeWidget()
        tree.setObjectName(object_name)
        tree.setHeaderHidden(True)
        tree.setIconSize(QSize(STATUS_DOT, STATUS_DOT))
        tree.setRootIsDecorated(True)
        tree.setIndentation(18)
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(
            lambda position, tree=tree: self._menu(position, tree)
        )
        tree.setVerticalScrollMode(QTreeWidget.ScrollPerPixel)
        # 单击只选中；双击（或回车）才只读浏览，所以双击不再折叠/展开行。
        tree.setExpandsOnDoubleClick(False)
        tree.itemActivated.connect(self.browse)
        tree.currentItemChanged.connect(self.select)
        return tree

    def _hint(self, text):
        label = QLabel(text)
        label.setObjectName("sessionHint")
        label.setTextFormat(Qt.PlainText)
        label.setContentsMargins(8, 3, 8, 3)
        label.setVisible(False)
        return label

    def _session_pane(self):
        """上块＝已打开会话，下块＝历史记录；两块之间只有分割条，没有标题行。"""

        splitter = QSplitter(Qt.Vertical)
        splitter.setObjectName("sessionSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._pane(self.current, self.current_hint))
        splitter.addWidget(self._pane(self.list, self.history_hint))
        # 默认 1:2（等分割条拿到高度后一次性分配），用户可拖动分割条自行调整。
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        self.splitter = splitter
        pane = QWidget()
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.notices)
        layout.addWidget(splitter, 1)
        return pane

    def _apply_default_split(self):
        """Split 1:2 once the widget has a real height; dragging wins afterwards."""

        if self._split_ready:
            return
        total = self.splitter.height()
        if total <= 1:
            return
        self._split_ready = True
        current = max(1, round(total / 3))
        self.splitter.setSizes([current, max(1, total - current)])

    def _pane(self, tree, hint):
        pane = QWidget()
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(hint)
        layout.addWidget(tree, 1)
        return pane

    def refresh(self):
        """Refresh the passive history tree without letting Qt slot errors escape."""
        try:
            self._apply_default_split()
            self._refresh()
            self._clear_alert("catalog")
        except Exception as exc:  # PyQt aborts the process for uncaught slot errors.
            self.catalog_state = None
            self._alert_once("catalog", str(exc), "会话历史暂不可用")

    def _progress(self, text):
        try:
            self.window.status.setText(text)
        except (AttributeError, RuntimeError):
            pass  # The window may already have been destroyed by a nested Qt event.

    @staticmethod
    def _unavailable_text(reason):
        if reason == "内容不完整，仅可删除":
            return "此会话内容不完整，不能浏览或恢复；请右键选择“删除”清理残留记录。"
        return f"此会话{reason}，无法浏览或操作，请选择其他会话。"

    _unavailable_live_text = staticmethod(unavailable_live_text)

    def _alert(self, text, title="会话不可用"):
        """Explicit actions share InfoApp; background refresh stays passive."""
        self.window.info_app.warning(title, text)

    def _alert_once(self, key, text, title="会话不可用"):
        """Background refresh reports in place; it must never map a dialog.

        A modeless dialog can still activate the application and switch the
        desktop. Hiding/dismissing it also made the old visibility-based guard
        create it again on the next refresh.
        """
        self.notices.set(key, title, text)

    def _clear_alert(self, key):
        self.notices.clear(key)

    def _disable_input(self):
        self.window.input.setEnabled(False)
        self.window.send.setEnabled(False)

    def _sync_input_scope(self):
        """输入框只读跟随选中行：停留在现场会话（或没有选中）时才能打字。"""

        selected = self.selected_id
        live = self.window.page.session.session_id
        self.window.input.setReadOnly(bool(selected) and selected != live)

    def _refresh(self):
        sessions = self.window.api
        opened = {handle.session_id: handle for handle in sessions.opened_sessions()}
        ended = {key for key, handle in opened.items() if sessions.is_closing(handle)}
        # 上块＝本窗口打开过的会话；服务里仍活着但不是本窗口打开的（上次运行的遗留）归下块。
        mine = self.window.page.opened
        live = self.window.page.session.session_id
        if live != self._live_id:
            # 激活/切换会话后选中跟随现场会话，旧选中不会一直高亮。
            self._clear_alert(self._live_id)
            self._live_id = live
            self.selected_id = ""
        browsing = self.window.page.reviewing
        selected = self.selected_id or browsing or live
        snapshot = sessions.catalog_snapshot(self.catalog_version)
        if snapshot is not None:
            if snapshot["error"]:
                raise ValueError(snapshot["error"])
            self.catalog_snapshot = snapshot
            self.catalog_version = snapshot["version"]
        rows = [dict(row) for row in self.catalog_snapshot["rows"]]
        by_id = {row["id"]: row for row in rows}
        keys = (sessions.initial_id, live, browsing)
        if getattr(self.window, "service_connection", None) is not None:
            keys += tuple(opened)
        for key in dict.fromkeys(keys):
            controller = opened.get(key)
            if controller is not None and (key not in by_id or key in ended):
                retained = dict(self.catalog_snapshot["retained"].get(key, {"id": key}))
                reason = self.catalog_snapshot.get("availability", {}).get(key, "")
                if reason and not retained.get("unavailable"):
                    retained["unavailable"] = reason
                if key in by_id:
                    by_id[key].update(retained)
                else:
                    rows.append(retained)
                    by_id[key] = retained
        current_row = by_id.get(sessions.initial_id)
        if current_row is not None:
            rows = [current_row] + [r for r in rows if r["id"] != sessions.initial_id]
        live_row = by_id.get(live) or {}
        if live_row.get("unavailable") or live in ended:
            self._catalog_blocked_runtime = self.window.page.session.runtime_id
            self._disable_input()
            self._alert_once(
                live,
                self._unavailable_live_text(live_row, ended=live in ended),
                "当前会话不可用",
            )
        else:
            self._clear_alert(live)
            blocked = self._catalog_blocked_runtime
            self._catalog_blocked_runtime = None
            window = self.window
            if (blocked == window.page.session.runtime_id and window.page.reviewing is None
                    and not window.page.opening and not window.lifecycle.closing
                    and not window.api.is_closing(window.page.session) and not window.binding.failed
                    and window.presentation.state and not window.presentation.state["fault"]):
                window.input.setEnabled(True)
                window.update_session(window.presentation.state)
        if browsing:
            reason = by_id.get(browsing, {}).get("unavailable", "")
            if (not reason and browsing not in by_id
                    and self.catalog_snapshot.get("complete", False)):
                reason = self.catalog_snapshot["availability"].get(browsing, "记录不可用")
        else:
            reason = ""
        if reason:
            # 浏览中的记录在磁盘上消失：结束浏览，回到现场会话并说明原因。
            self._alert_once(
                "browse",
                "正在浏览的会话" + reason + "，已返回当前会话。",
                "浏览结束",
            )
            self.window.return_from_preview()
            browsing = self.window.page.reviewing
            selected = self.selected_id or browsing or live
        activities = {
            key: session_activity(sessions.activity(controller))
            for key, controller in opened.items()
        }

        def child_signature(children):
            return tuple(
                (c.get("thread_id", ""), c.get("name", ""), c.get("status", ""),
                 child_signature(c.get("children", [])))
                for c in children
            )

        signature = (
            selected,
            live,
            browsing,
            activities,
            ended,
            [
                (
                    r["id"],
                    r.get("name", ""),
                    r.get("title", ""),
                    _session_time(r.get("modified")),
                    r.get("unavailable", ""),
                    r.get("damaged", False),
                    child_signature(r.get("children", [])),
                )
                for r in rows
            ],
        )
        if signature == self.catalog_state:
            return
        current_position = self.current.verticalScrollBar().value()
        position = self.list.verticalScrollBar().value()
        blocked = [(tree, tree.blockSignals(True)) for tree in (self.current, self.list)]
        try:
            for tree, _state in blocked:
                tree.clear()
            for row in rows:
                key = row["id"]
                reason = row.get("unavailable", "")
                if reason == "已删除" and (key not in opened or
                        getattr(self.window, "service_connection", None) is not None):
                    # 记录已不存在：直接从导航移除，而不是留给用户点击。
                    continue
                stamp = _session_time(row.get("modified"))
                # 已结束的会话不再是本窗口“打开”的会话：它归档到下块，只留只读记录。
                # 上块＝本窗口打开过且未结束；其余（记录、已结束、上次运行遗留的活会话）留在下块。
                opened_here = key in mine and (key not in ended or bool(reason))
                title = (
                    "当前会话\n" + key[:12]
                    if key == live and opened_here
                    else stamp + "\n" + key[:12]
                )
                name = row.get("name") or row.get("title") or title
                if key == browsing:
                    name = name + "\n（浏览中 · 只读）"
                status = reason or ("已结束" if key in ended else "")
                if status:
                    name = name + "\n（" + status + "）"
                item = QTreeWidgetItem([name])
                lines = max(2, name.count("\n") + 1)
                item.setSizeHint(0, QSize(140, self.fontMetrics().height() * lines + 12))
                activity = activities.get(key, "idle")
                if reason or not opened_here:
                    # 下块是记录视图（历史/已结束/上次运行遗留）：圆点一律静默，不显示服务侧活动。
                    activity = "idle"
                item.setIcon(0, activity_icon(activity))
                item.setData(0, Qt.UserRole, key)
                item.setData(0, Qt.UserRole + 3, bool(row.get("damaged")))
                tooltip = activity_label(activity) + ("\n" + status if status else "") + "\n" + key
                if not reason:
                    tooltip += "\n双击浏览（只读）；右键“继续”切换或恢复该会话"
                item.setToolTip(0, tooltip)
                if reason:
                    item.setData(0, Qt.UserRole + 2, reason)
                    item.setForeground(0, QBrush(Qt.gray))
                    # 双保险：不可用的会话灰掉且不可选中，点击不再触发任何操作。
                    if row.get("damaged"):
                        item.setToolTip(0, tooltip + "\n内容不完整，右键仅可删除残留记录")
                    else:
                        item.setFlags(item.flags() & ~(Qt.ItemIsSelectable | Qt.ItemIsEnabled))
                tree = self.current if opened_here else self.list
                tree.addTopLevelItem(item)

                def add_child(parent_item, child):
                    child_id = child.get("thread_id", "")
                    if not child_id:
                        return
                    child_name = child.get("name") or "子任务"
                    child_item = QTreeWidgetItem([child_name + "\n" + child_id[:12]])
                    child_activity = CHILD_ACTIVITY.get(child.get("status", ""), "free")
                    child_item.setIcon(0, activity_icon(child_activity))
                    child_item.setToolTip(0, activity_label(child_activity) + "\n" + child_id)
                    child_item.setData(0, Qt.UserRole, child_id)
                    child_item.setData(0, Qt.UserRole + 1, key)
                    parent_item.addChild(child_item)
                    for nested in child.get("children", []):
                        add_child(child_item, nested)

                for child in row.get("children", []):
                    add_child(item, child)
                item.setExpanded(bool(row.get("children")))
            if not self._mark_selected(selected) and self.selected_id:
                # 选中的行已消失（删除或记录退出目录）：落回现场会话。
                self.selected_id = ""
                self._mark_selected(browsing or live)
        finally:
            for tree, state in blocked:
                tree.blockSignals(state)
        self.current_hint.setVisible(self.current.topLevelItemCount() == 0)
        self.history_hint.setVisible(self.list.topLevelItemCount() == 0)
        self.current.verticalScrollBar().setValue(current_position)
        self.list.verticalScrollBar().setValue(position)
        self.catalog_state = signature
        self._sync_input_scope()


    def _row(self, session_id):
        for row in self.catalog_snapshot["rows"]:
            if row["id"] == session_id:
                return row
        return self.catalog_snapshot["retained"].get(session_id, {})


    def _session_items(self):
        """Every session row in both panes, current pane first."""

        for tree in (self.current, self.list):
            for index in range(tree.topLevelItemCount()):
                yield tree.topLevelItem(index)

    def _mark_selected(self, key):
        """Highlight the row that owns ``key`` (a session or child thread)."""

        if not key:
            return False
        for tree in (self.current, self.list):
            for item in _deep_items(tree):
                if item.data(0, Qt.UserRole) == key:
                    tree.setCurrentItem(item)
                    return True
        return False


    def select(self, item, previous=None):
        """Single click only highlights a row; browsing waits for a double-click."""

        if item is None:
            return
        # 两棵树共享单选：另一棵树上的高亮先清掉，避免出现两个选中。
        for tree in (self.current, self.list):
            if tree.currentItem() is not None and tree.currentItem() is not item:
                blocked = tree.blockSignals(True)
                tree.setCurrentItem(None)
                tree.blockSignals(blocked)
        try:
            key = item.data(0, Qt.UserRole)
            unavailable = item.data(0, Qt.UserRole + 2)
            if unavailable:
                self._alert(self._unavailable_text(unavailable))
                self.catalog_state = None
                return
            self.selected_id = key
            self._sync_input_scope()
        except Exception as exc:  # A Qt signal/slot must never abort the worker.
            self._alert("历史会话暂不可读：" + str(exc))
            self.catalog_state = None

    def browse(self, item=None, _column=None):
        """Double-click/Enter opens the read-only replay; a single click only selects."""

        if item is None:
            return
        try:
            window = self.window
            key = item.data(0, Qt.UserRole)
            parent_id = item.data(0, Qt.UserRole + 1)
            session_id = parent_id or key
            reason = item.data(0, Qt.UserRole + 2) or self._row(session_id).get("unavailable", "")
            if reason:
                self._alert(self._unavailable_text(reason))
                self.catalog_state = None
                return
            self._clear_alert("browse")
            self.selected_id = session_id
            self._sync_input_scope()
            window.preview_session(session_id)
            self.catalog_state = None
            self.refresh()
        except Exception as exc:  # A Qt signal/slot must never abort the worker.
            self._alert("历史会话暂不可读：" + str(exc))
            self.catalog_state = None
