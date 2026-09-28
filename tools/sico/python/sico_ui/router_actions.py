"""Router receipt queries and scoped cancellation presentation."""

from threading import Lock

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QPlainTextEdit, QVBoxLayout

from .chrome import SiDialog


class RouterActions:
    def __init__(self, widgets, *, page, presentation, lifecycle, stream_failed,
                 run_command, receipt_action=None):
        self.ui = widgets
        self.page, self.presentation, self.lifecycle = page, presentation, lifecycle
        self.stream_failed, self.run_command = stream_failed, run_command
        self.receipt_action = receipt_action
        self._router_cancellations = {}
        self._router_cancel_lock = Lock()
        self._router_receipt_query = None
        self._router_receipt_dialog = None

    def router_receipt_scope(self):
        state = self.presentation.state
        if (state is None or self.page.reviewing is not None or self.page.opening or self.lifecycle.closing
                or state["closing"] or self.stream_failed()):
            return None
        return (self.page.session.session_id, self.page.session.runtime_id,
                self.page.activation, self.presentation.displayed_context)

    def refresh_router_receipt(self):
        scope = self.router_receipt_scope()
        if self._router_receipt_query and self._router_receipt_query[0] != scope:
            self._router_receipt_query = None
        if self._router_receipt_dialog and self._router_receipt_dialog[0] != scope:
            self._router_receipt_dialog[1].close()
        if self.receipt_action is not None:
            self.receipt_action.setEnabled(scope is not None and self._router_receipt_query is None)

    def query_router_receipt(self):
        scope = self.router_receipt_scope()
        if scope is None or (self._router_receipt_query and self._router_receipt_query[0] == scope):
            return
        controller, context = self.page.session, self.presentation.displayed_context
        query = (scope, object())
        self._router_receipt_query = query
        self.refresh_router_receipt()

        def current():
            return query == self._router_receipt_query and scope == self.router_receipt_scope()

        def failed(exc):
            if current():
                self._router_receipt_query = None
                self.refresh_router_receipt()
                self.ui.status_bar.clearMessage()
                self.ui.notices.error("回执查询失败", str(exc))
            return False

        def show(receipt):
            if not current():
                return
            if (receipt.session_id != scope[0] or receipt.runtime_id != scope[1]
                    or receipt.context != context):
                failed(ValueError("回执来源不匹配，请重新查询"))
                return
            self._router_receipt_query = None
            self.refresh_router_receipt()
            if self._router_receipt_dialog:
                self._router_receipt_dialog[1].close()
            dialog = SiDialog(receipt.title, self.ui.parent, ("close",))
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            layout = QVBoxLayout()
            layout.setContentsMargins(12, 12, 12, 12)
            dialog.content_layout().addLayout(layout)
            text = QPlainTextEdit()
            text.setObjectName("routerReceipt")
            text.setReadOnly(True)
            text.setPlainText(receipt.text)
            layout.addWidget(text)
            dialog.resize(680, 480)
            self._router_receipt_dialog = (scope, dialog)

            def closed(_result):
                if self._router_receipt_dialog and self._router_receipt_dialog[1] is dialog:
                    self._router_receipt_dialog = None

            dialog.finished.connect(closed)
            self.ui.status_bar.clearMessage()
            dialog.show()

        future = self.run_command("router_receipt", controller=controller, context=context,
                                  formatted=True, success=show, failure=failed)
        if future is not None:
            self.ui.status_bar.showMessage("正在查询路由回执…", 5000)
        return future

    def router_cancel_scope(self):
        state = self.presentation.state or {}
        if (not state or self.page.opening or self.page.reviewing is not None or self.lifecycle.closing
                or state["closing"] or state["fault"] or state["cancelling"]
                or self.stream_failed()):
            return None
        context = self.presentation.displayed_context
        router = state.get("router") or {}
        if (router.get("state") == "router_unavailable"
                or any(router.get(key) != getattr(context, key)
                       for key in ("instance_id", "generation", "target_id"))):
            return None
        return self.page.session.runtime_id, self.page.activation, context, router.get("router_id")

    def refresh_task_panel(self):
        self.refresh_router_receipt()
        state = self.presentation.state or {}
        router = state.get("router")
        runtime_id = self.page.session.runtime_id
        queued = {request["request_id"] for request in (router or {}).get("requests", [])
                  if request["state"] in {"queued", "cancel_requested"}}
        with self._router_cancel_lock:
            for key, receipt in list(self._router_cancellations.items()):
                if key[0] == runtime_id and receipt is not None and key[1] not in queued:
                    del self._router_cancellations[key]
            cancelling = [key[1] for key in self._router_cancellations if key[0] == runtime_id]
        self.ui.task_panel.update_requests(
            state.get("requests", []), router if self.page.reviewing is None else None,
            cancel_scope=self.router_cancel_scope(),
            cancelling=cancelling,
        )

    def cancel_router_request(self, request_id, scope):
        if scope is None or scope != self.router_cancel_scope():
            return
        controller = self.page.session
        runtime_id, _, context, _ = scope
        key = (runtime_id, request_id)
        state = self.presentation.state
        task_ids = {row.get("task_id") for row in state["requests"] if row.get("task_id")}
        if not any(request["request_id"] == request_id and request["state"] == "queued"
                   and request.get("session_id") == controller.session_id
                   and request.get("target_id") == context.target_id
                   and request.get("task_id") in task_ids
                   for request in state["router"]["requests"]):
            return
        with self._router_cancel_lock:
            if key in self._router_cancellations:
                return
            self._router_cancellations[key] = None
        self.refresh_task_panel()
        self.ui.status_bar.showMessage("正在提交取消请求…", 5000)

        def completed(receipt):
            if controller == self.page.session:
                self.refresh_task_panel()
            if scope == self.router_cancel_scope():
                message = {
                    "cancel_requested": "排队请求已取消，等待任务回执",
                    "running": "请求已开始执行，无法取消排队",
                    "running_unknown": "执行结果待确认，占用仍保留",
                    "not_owner": "取消被拒绝：请求不属于当前会话或目标",
                    "not_found": "请求已离开队列，请查看任务记录",
                    "router_unavailable": "连接已失效，取消未确认",
                }.get(receipt, "取消未确认：未知回执")
                self.ui.status_bar.clearMessage()
                notify = (self.ui.notices.info if receipt in {"cancel_requested", "not_found"}
                          else self.ui.notices.warning)
                notify("取消请求结果", message)

        def failed(exc):
            if controller == self.page.session:
                self.refresh_task_panel()
            if scope == self.router_cancel_scope():
                self.ui.status_bar.clearMessage()
                self.ui.notices.error("取消未提交", str(exc))
            return False

        def settled(receipt, error):
            with self._router_cancel_lock:
                if error is None:
                    self._router_cancellations[key] = receipt
                else:
                    self._router_cancellations.pop(key, None)

        return self.run_command(
            "cancel_router_request", request_id, context=context, controller=controller,
            success=completed, failure=failed, settled=settled,
        )
