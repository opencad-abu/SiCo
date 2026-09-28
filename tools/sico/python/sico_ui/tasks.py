"""Task queue/history presentation, driven only by controller snapshots."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QMenu, QStyle, QTreeWidget, QTreeWidgetItem

from .presentation import target_label

STATUS = {
    "queued": "已接收 · 排队中",
    "executing": "执行中",
    "waiting_user": "等待答复",
    "completed": "已完成",
    "cancelled": "已取消",
    "failed": "失败",
    "needs_reconcile": "中断 · 待核对",
    "not_started": "未执行 · 不自动重放",
}


def router_request_label(request):
    state = request.get("state")
    if state == "queued":
        return f"等待 Virtuoso · 第 {request['queue_position']} 位"
    return {
        "running": "Virtuoso 执行中",
        "cancel_requested": "正在取消等待中的请求",
        "timed_out_unknown": "Virtuoso 执行结果待确认",
    }.get(state, "")


def router_request_short_label(request):
    """Keep the live state readable in the narrow task tree."""
    state = request.get("state")
    if state == "queued":
        return f"Virtuoso · 第{request['queue_position']}位"
    return {
        "running": "Virtuoso执行中",
        "cancel_requested": "Virtuoso取消中",
        "timed_out_unknown": "Virtuoso待确认",
    }.get(state, "Virtuoso")


def router_tooltip(router):
    if not router:
        return ""
    labels = {
        "idle": "空闲", "running": "执行中", "queued": "排队中",
        "blocked_unknown": "执行结果待确认", "router_unavailable": "未连接",
    }
    lines = ["Virtuoso：" + labels.get(router["state"], router["state"]),
             "实例：" + router["instance_id"], "连接代次：" + router["generation"]]
    owner = router.get("owner")
    if owner:
        lines.extend(["占用请求：" + owner["request_id"],
                      "所属会话：" + owner.get("session_id", "未关联会话"),
                      "执行目标：" + owner.get("target_id", "未关联目标")])
        started = owner.get("started_at")
        if isinstance(started, (int, float)):
            lines.append(
                "开始时间：" + datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S")
            )
    lines.append(f"实例等待请求：{router['queued']}")
    for request in router["requests"]:
        lines.append("本会话请求：" + request["request_id"] + " · " + router_request_label(request))
    return "\n".join(lines)


class TaskPanel(QTreeWidget):
    cancelRequested = pyqtSignal(str, object)

    def __init__(self):
        super().__init__()
        self.setObjectName("taskQueue")
        self.setHeaderLabels(["状态", "来源设计", "需求"])
        self.setRootIsDecorated(False)
        self.setVerticalScrollMode(self.ScrollPerPixel)
        self.setColumnWidth(0, 160)
        self.setColumnWidth(1, 300)
        self.setHeaderHidden(True)
        self.hideColumn(1)
        self.hideColumn(2)
        self.setWordWrap(True)
        self.setMinimumHeight(100)
        self.items = {}
        self._router_requests = {}
        self._cancel_scope = None
        self._cancelling = set()
        self._cancel_menu = None
        self._paint_timer = QTimer(self)
        self._paint_timer.setSingleShot(True)
        self._paint_timer.timeout.connect(self._paint_rows)
        self._paint_rows_pending = []
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    def update_requests(self, rows, router=None, *, cancel_scope=None, cancelling=()):
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

    def _menu(self, position):
        item = self.itemAt(position)
        if item is None or self._cancel_scope is None:
            return
        task_id = item.data(0, Qt.UserRole + 1)
        requests = [request for request in self._router_requests.values()
                    if task_id and request.get("task_id") == task_id
                    and request["state"] in {"queued", "cancel_requested"}]
        if not requests:
            return
        if self._cancel_menu:
            self._cancel_menu[0].close()
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        scope, actions = self._cancel_scope, {}
        for request in requests:
            request_id = request["request_id"]
            action = menu.addAction(self.style().standardIcon(QStyle.SP_DialogCancelButton), "")
            action.triggered.connect(
                lambda _checked=False, key=request_id: self.cancelRequested.emit(key, scope)
            )
            actions[request_id] = action
        self._cancel_menu = (menu, scope, actions)

        def closed():
            if self._cancel_menu and self._cancel_menu[0] is menu:
                self._cancel_menu = None
            menu.deleteLater()

        menu.aboutToHide.connect(closed)
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


def session_status(state):
    if state["fault"]:
        return "会话故障"
    if state["closing"]:
        return "正在结束会话…" if state["busy"] else "会话已结束"
    if state["cancelling"]:
        return "正在取消…"
    router = state.get("router") or {}
    if router.get("state") == "blocked_unknown":
        return "Virtuoso 执行结果待确认"
    if router.get("state") == "router_unavailable":
        return "Virtuoso 未连接"
    if state["task"].get("status") == "needs_reconcile":
        return "任务已中断 · 待核对"
    if state["busy"]:
        if state["task"].get("waiting_audits"):
            return "等待答复" + (
                f"；待办 {state['pending']} 条" if state["pending"] else ""
            )
        requests = [request for request in router.get("requests", [])
                    if request.get("task_id") and request["task_id"] == state["task"].get("id")]
        if requests:
            label = router_request_label(requests[0])
            return label + (f"；待办 {state['pending']} 条" if state["pending"] else "")
        activity = state["task"].get("activity") or {}
        label = activity.get("label") or "正在处理任务"
        tool = activity.get("tool")
        if tool:
            label += f"：{tool}"
        turn = activity.get("turn")
        if isinstance(turn, int) and turn > 0:
            label += f"（第 {turn} 轮）"
        started = activity.get("started_at")
        if isinstance(started, str):
            try:
                if started.endswith("Z"):
                    started = started[:-1] + "+00:00"
                elapsed = max(
                    0,
                    int(
                        (
                            datetime.now(timezone.utc) - datetime.fromisoformat(started)
                        ).total_seconds()
                    ),
                )
                label += f" · {elapsed}s"
                if activity.get("phase") == "waiting_model" and elapsed >= 60:
                    label += " · 尚未收到可用答复"
            except ValueError:
                pass
        return label + (f"；队列 {state['pending']} 条" if state["pending"] else "")
    status = state["task"].get("status")
    label = "未执行" if status == "not_started" else STATUS.get(status, "就绪")
    if state["paused"] and state["pending"]:
        label += f"；待办 {state['pending']} 条（已暂停）"
    return label
