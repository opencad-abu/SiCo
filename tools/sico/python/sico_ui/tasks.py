"""Task queue/history presentation, driven only by controller snapshots."""

from __future__ import annotations

import time

from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QBrush
from PyQt5.QtWidgets import (
    QApplication, QHeaderView, QMenu, QStyle, QTreeWidget, QTreeWidgetItem,
)

from .presentation import target_label
from .task_filter import TaskFilterMixin

from .task_status import (
    STATUS, TREE_STATUS, execution_status, status_color,
    router_request_label, router_request_short_label, router_tooltip,
)
from .task_status import session_status as session_status

CHILD_STATUS = {
    "started": "执行中",
    "completed": "已完成",
    "failed": "失败",
    "interrupted": "中断",
    "closed": "已关闭",
}


class TaskPanel(TaskFilterMixin, QTreeWidget):
    cancelRequested = pyqtSignal(str, object)
    nodeActivated = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.setObjectName("taskQueue")
        self.setHeaderLabels(["任务", "状态", "来源设计"])
        self.setRootIsDecorated(True)
        self.setIndentation(14)
        self.setVerticalScrollMode(self.ScrollPerPixel)
        self.setHeaderHidden(True)
        self.header().setStretchLastSection(False)
        self.setTextElideMode(Qt.ElideRight)
        self.setMinimumHeight(100)
        self.items = {}
        self._nodes = {}
        self._tree_mode = False
        self._session_id = None
        self._expanded = set()
        self._router_requests = {}
        self._cancel_scope = None
        self._cancelling = set()
        self._cancel_menu = None
        self._paint_timer = QTimer(self)
        self._paint_timer.setSingleShot(True)
        self._paint_timer.timeout.connect(self._paint_rows)
        self._paint_rows_pending = []
        self._filter_text = ""
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemClicked.connect(self._clicked)
        self.itemActivated.connect(self._activate)
        self.itemExpanded.connect(self._remember_expanded)
        self.itemCollapsed.connect(self._remember_collapsed)
        self._flat_columns()

    def bind_session(self, session_id):
        """A task panel belongs to exactly one selected session."""
        session_id = str(session_id or "")
        if session_id == self._session_id:
            return
        self._session_id = session_id
        self._paint_timer.stop()
        self._paint_rows_pending = []
        self.blockSignals(True)
        self.clear()
        self.items.clear()
        self._nodes.clear()
        self._expanded.clear()
        self._router_requests.clear()
        self._tree_mode = False
        self.blockSignals(False)

    def _flat_columns(self):
        self.setWordWrap(True)
        self.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.setColumnWidth(0, 160)
        self.setColumnWidth(1, 300)
        self.hideColumn(1)
        self.hideColumn(2)

    def _tree_columns(self):
        self.setWordWrap(False)
        self.showColumn(0)
        self.showColumn(1)
        self.hideColumn(2)
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.Fixed)
        self.setColumnWidth(1, 90)

    def update_requests(self, rows, router=None, *, cancel_scope=None, cancelling=(),
                        session_id=None):
        if session_id is not None:
            self.bind_session(session_id)
        rows = [
            row for row in rows
            if row.get("origin") not in {"startup", "thread_operation"}
        ]
        if self._tree_mode:
            self.blockSignals(True)
            self.clear()
            self.items.clear()
            self._nodes.clear()
            self._expanded.clear()
            self._tree_mode = False
            self.blockSignals(False)
        self._flat_columns()
        self._router_requests = {request["request_id"]: request
                                 for request in (router or {}).get("requests", [])}
        self._cancel_scope = cancel_scope
        self._cancelling = set(cancelling)
        routed = {request["task_id"]: request
                  for request in (router or {}).get("requests", []) if request.get("task_id")}
        keys = {row["id"] for row in rows}
        for key in list(self.items):
            if key not in keys:
                self.takeTopLevelItem(self.indexOfTopLevelItem(self.items.pop(key)))
        self._paint_timer.stop()
        self._paint_rows_pending = list(reversed(rows))
        self._paint_routed, self._paint_router = routed, router
        self._paint_rows()
        self._apply_filter()

    def update_tree(self, tree, router=None, *, cancel_scope=None, cancelling=(),
                    current_task_id="", session_id=None):
        """Render the Work/Stage/Execution tree; flat rows remain the fallback."""

        if session_id is not None:
            self.bind_session(session_id)
        self._tree_mode = True
        self._tree_columns()
        self._paint_timer.stop()
        self._paint_rows_pending = []
        self._router_requests = {request["request_id"]: request
                                 for request in (router or {}).get("requests", [])}
        self._cancel_scope = cancel_scope
        self._cancelling = set(cancelling)
        routed = {request["task_id"]: request
                  for request in (router or {}).get("requests", []) if request.get("task_id")}
        selected = self.currentItem().data(0, Qt.UserRole) if self.currentItem() else ""
        saved = set(self._expanded)
        self.blockSignals(True)
        self.clear()
        self.items.clear()
        self._nodes.clear()
        work_items = {}
        execution_items = {}

        def item(key, kind, target, title, status, tooltip, *, task_id="",
                 status_key="", lines=1):
            node = QTreeWidgetItem([title, status])
            node.setData(0, Qt.UserRole, key)
            node.setData(0, Qt.UserRole + 1, task_id)
            node.setData(0, Qt.UserRole + 2, kind)
            node.setData(0, Qt.UserRole + 3, target)
            node.setSizeHint(0, QSize(0, self.fontMetrics().lineSpacing() * lines + 8))
            node.setToolTip(0, tooltip)
            node.setToolTip(1, status)
            node.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            color = status_color(status_key)
            if color is not None:
                node.setForeground(1, QBrush(color))
            node.setData(0, Qt.AccessibleTextRole,
                         (title + (" · " + status if status else "")).strip())
            self._nodes[key] = node
            return node

        for work in tree.get("works") or ():
            work_id = str(work.get("id", ""))
            work_key = "work:" + work_id
            stages = work.get("stages") or ()
            work_text = str(work.get("title", "")) + " · " + str(len(stages)) + " 阶段"
            work_status = TREE_STATUS.get(work.get("status", ""), "未提供")
            work_item = item(
                work_key, "work", work_id, work_text, work_status,
                str(work.get("title", "")) + "\n状态："
                + TREE_STATUS.get(work.get("status", ""), "未提供"),
                status_key=work.get("status", ""),
            )
            self.addTopLevelItem(work_item)
            work_items[work_key] = work_item
            stage_titles = {str(row.get("id", "")): str(row.get("title", ""))
                            for row in stages}
            for stage in stages:
                stage_id = str(stage.get("id", ""))
                stage_key = "stage:" + stage_id
                executions = stage.get("executions") or ()
                count = (" · " + str(len(executions)) + " 次"
                         if len(executions) > 1 else "")
                if stage.get("conflict"):
                    status_label, status_key = "冲突", "blocked"
                else:
                    status_label = TREE_STATUS.get(stage.get("status", ""), "未提供")
                    status_key = stage.get("status", "")
                stage_text = str(stage.get("title", "")) + count
                stage_tip = (
                    str(stage.get("title", "")) + "\n状态："
                    + TREE_STATUS.get(stage.get("status", ""), "未提供")
                    + "\n目标：" + str(stage.get("objective", "") or "未提供")
                    + "\n验收条件：" + str(stage.get("acceptance", "") or "未提供")
                )
                for key, label in (("reason", "原因"), ("blocker", "阻塞"),
                                   ("next_step", "下一步")):
                    if stage.get(key):
                        stage_tip += "\n" + label + "：" + str(stage[key])
                if stage.get("plan_revision"):
                    stage_tip += "\n计划版本：" + str(stage["plan_revision"])
                dependencies = [
                    stage_titles.get(value, value)
                    for value in stage.get("depends_on") or []
                ]
                if dependencies:
                    stage_tip += "\n依赖阶段：" + "、".join(dependencies)
                if stage.get("conflict"):
                    stage_tip += "\n冲突：模型判断与执行/报告事实不一致"
                stage_item = item(
                    stage_key, "stage", stage_id, stage_text, status_label, stage_tip,
                    status_key=status_key,
                )
                work_item.addChild(stage_item)
                for execution in executions:
                    task_id = str(execution.get("task_id", ""))
                    input_id = str(execution.get("id", ""))
                    key = "execution:" + (task_id or input_id)
                    request = routed.get(task_id)
                    status_label, status_key = execution_status(execution, request)
                    title = ("初始化上下文" if execution.get("origin") == "startup"
                             else str(execution.get("text", "")))
                    source = str(execution.get("origin", ""))
                    detail = "\n" + router_tooltip(router) if request else ""
                    if execution.get("inherited"):
                        detail += "\n未显式选择阶段；队列按上一阶段显示"
                    execution_item = item(
                        key, "execution", input_id,
                        title[:80], status_label,
                        source + "\n" + title + "\n"
                        + str(execution.get("diagnostic", "")) + detail,
                        task_id=task_id, status_key=status_key,
                    )
                    stage_item.addChild(execution_item)
                    execution_items[key] = execution_item
                    for child in execution.get("children") or ():
                        thread_id = str(child.get("thread_id", ""))
                        child_key = "child:" + task_id + ":" + thread_id
                        child_item = item(
                            child_key, "child", thread_id,
                            str(child.get("name", ""))[:80],
                            CHILD_STATUS.get(child.get("status", ""),
                                             child.get("status", "")),
                            "Codex 子任务\n线程：" + thread_id
                            + "\n" + str(child.get("name", "")),
                            task_id=task_id, lines=2,
                        )
                        execution_item.addChild(child_item)

        unassigned = tree.get("unassigned") or ()
        if unassigned:
            group_key = "work:__unassigned__"
            group = item(group_key, "group", "", "未关联工程任务", "", "")
            self.addTopLevelItem(group)
            stage_key = "stage:__unassigned__"
            stage_item = item(stage_key, "group", "", "未关联阶段", "", "")
            group.addChild(stage_item)
            for execution in unassigned:
                task_id = str(execution.get("task_id", ""))
                input_id = str(execution.get("id", ""))
                key = "execution:" + (task_id or input_id)
                request = routed.get(task_id)
                status_label, status_key = execution_status(execution, request)
                title = ("初始化上下文" if execution.get("origin") == "startup"
                         else str(execution.get("text", "")))
                detail = "\n" + router_tooltip(router) if request else ""
                if execution.get("inherited"):
                    detail += "\n未显式选择阶段；队列按上一阶段显示"
                execution_item = item(
                    key, "execution", input_id, title[:80], status_label,
                    title + "\n" + str(execution.get("diagnostic", "")) + detail,
                    task_id=task_id, status_key=status_key,
                )
                stage_item.addChild(execution_item)
                execution_items[key] = execution_item
                for child in execution.get("children") or ():
                    thread_id = str(child.get("thread_id", ""))
                    child_item = item(
                        "child:" + task_id + ":" + thread_id, "child", thread_id,
                        str(child.get("name", ""))[:80],
                        CHILD_STATUS.get(child.get("status", ""),
                                         child.get("status", "")),
                        "Codex 子任务\n线程：" + thread_id
                        + "\n" + str(child.get("name", "")),
                        task_id=task_id, lines=2,
                    )
                    execution_item.addChild(child_item)

        current_key = ""
        if current_task_id:
            current_key = next(
                (key for key, node in execution_items.items()
                 if node.data(0, Qt.UserRole + 1) == current_task_id), ""
            )
        expanded = set(saved)
        if current_key:
            node = execution_items[current_key]
            while node is not None:
                expanded.add(node.data(0, Qt.UserRole))
                node = node.parent()
        elif not saved:
            for work_key, node in work_items.items():
                expanded.add(work_key)
                if node.childCount():
                    expanded.add(node.child(0).data(0, Qt.UserRole))

        for key, node in self._nodes.items():
            node.setExpanded(key in expanded)
        if selected:
            node = self._nodes.get(selected)
            if node is not None:
                self.setCurrentItem(node)
        self._expanded = {
            key for key, node in self._nodes.items() if node.isExpanded()
        }
        self.blockSignals(False)
        self._refresh_cancel_menu()
        self._apply_filter()

    def _paint_rows(self):
        row_size = QSize(0, self.fontMetrics().lineSpacing() * 3 + 8)
        routed, router = self._paint_routed, self._paint_router
        deadline, count = time.monotonic() + 0.008, 0
        while self._paint_rows_pending:
            row = self._paint_rows_pending.pop()
            item = self.items.get(row["id"])
            if item is None:
                item = QTreeWidgetItem()
                item.setData(0, Qt.UserRole, row["id"])
                self.addTopLevelItem(item)
                self.items[row["id"]] = item
            item.setData(0, Qt.UserRole + 1, row.get("task_id"))
            source = target_label(row["context"]["snapshot"]).removeprefix("当前目标：")
            title = "初始化上下文" if row.get("origin") == "startup" else row["text"]
            label = STATUS.get(row["status"], row["status"])
            request = routed.get(row.get("task_id"))
            if request:
                label = router_request_short_label(request)
            item.setSizeHint(0, row_size)
            item.setText(0, label + "\n" + title[:80])
            item.setText(1, source)
            item.setText(2, " ".join(row["text"].split())[:160])
            item.setData(0, Qt.AccessibleTextRole, label + " " + title[:80])
            detail = "\n" + router_tooltip(router) if request else ""
            item.setToolTip(0, source + "\n" + title + "\n" + row.get("diagnostic", "") + detail)
            item.setToolTip(1, source)
            item.setToolTip(2, row["text"])
            count += 1
            if count >= 16 or time.monotonic() >= deadline:
                break
        if self._paint_rows_pending:
            self._paint_timer.start(10)
        self._refresh_cancel_menu()
        if self._filter_text:
            self._apply_filter()

    def _clicked(self, item, _column=0):
        if QApplication.mouseButtons() & Qt.RightButton:
            return
        self._activate(item)

    def _activate(self, item, _column=0):
        if item is None:
            return
        kind = item.data(0, Qt.UserRole + 2)
        target = item.data(0, Qt.UserRole + 3)
        task_id = item.data(0, Qt.UserRole + 1)
        if kind == "work" and target:
            self.nodeActivated.emit("work", str(target))
        elif kind == "stage" and target:
            self.nodeActivated.emit("stage", str(target))
        elif kind == "execution":
            if task_id:
                self.nodeActivated.emit("task", str(task_id))
            else:
                self.nodeActivated.emit("queued", str(target))
        elif kind == "child" and task_id:
            self.nodeActivated.emit("task", str(task_id))

    def _remember_expanded(self, item):
        key = item.data(0, Qt.UserRole)
        if key:
            self._expanded.add(key)

    def _remember_collapsed(self, item):
        key = item.data(0, Qt.UserRole)
        self._expanded.discard(key)

    def _menu(self, position):
        item = self.itemAt(position)
        if item is None:
            return
        task_id = item.data(0, Qt.UserRole + 1)
        requests = [request for request in self._router_requests.values()
                    if task_id and request.get("task_id") == task_id
                    and request["state"] in {"queued", "cancel_requested"}]
        if self._cancel_menu:
            self._cancel_menu[0].close()
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        scope, actions = self._cancel_scope, {}
        for request in requests:
            if scope is None:
                break
            request_id = request["request_id"]
            action = menu.addAction(self.style().standardIcon(QStyle.SP_DialogCancelButton), "")
            action.triggered.connect(
                lambda _checked=False, key=request_id: self.cancelRequested.emit(key, scope)
            )
            actions[request_id] = action
        if actions:
            self._cancel_menu = (menu, scope, actions)
        else:
            self._cancel_menu = None
        if menu.actions():
            menu.addSeparator()
        filter_action = menu.addAction("筛选任务…")
        clear_action = menu.addAction("清除筛选")
        clear_action.setEnabled(bool(self._filter_text))
        filter_action.triggered.connect(lambda _checked=False: self._ask_filter())
        clear_action.triggered.connect(lambda _checked=False: self.set_filter(""))

        def closed():
            if self._cancel_menu and self._cancel_menu[0] is menu:
                self._cancel_menu = None
            menu.deleteLater()

        menu.aboutToHide.connect(closed)
        if actions:
            self._refresh_cancel_menu()
        menu.popup(self.viewport().mapToGlobal(position))

    def _refresh_cancel_menu(self):
        if self._cancel_menu is None:
            return
        menu, scope, actions = self._cancel_menu
        if scope != self._cancel_scope:
            menu.close()
            return
        for request_id, action in actions.items():
            request = self._router_requests.get(request_id, {})
            pending = request_id in self._cancelling or request.get("state") == "cancel_requested"
            action.setEnabled(request.get("state") == "queued" and not pending)
            label = {
                "queued": "取消排队请求", "cancel_requested": "取消已提交",
                "running": "请求已开始执行", "timed_out_unknown": "执行结果待确认",
            }.get(request.get("state"), "请求已离开队列")
            position = request.get("queue_position")
            suffix = f" · 第 {position} 位" if len(actions) > 1 and position else ""
            action.setText(label + suffix)
            action.setToolTip(request_id + "\n" + router_request_label(request))
