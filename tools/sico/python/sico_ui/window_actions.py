"""Window command, target selection and command receipt presentation."""

import uuid

from PyQt5.QtWidgets import QDialog

from .receipt_scope import ReceiptScope
from .pending_commands import PendingCommands
from .si_prompt import confirm
from .steering import DEFER_HINT, WAIT_HINT
from .target_picker import NEW_WINDOW_OPTION, SCHEMATIC_FAMILY, TargetPicker


class WindowActions:
    OPEN_AUDIT_STATUSES = ("prepared", "pending", "answer_received")

    def __init__(self, widgets, *, api, page, presentation, lifecycle, receipts,
                 drafts, submissions, centers, poll, stream_failed, steering, update, show_panel):
        self.ui = widgets
        self.api = api
        self.page = page
        self.presentation = presentation
        self.lifecycle = lifecycle
        self.commands = receipts
        self.drafts = drafts
        self.centers = centers
        self.poll = poll
        self.stream_failed = stream_failed
        self.steering = steering
        self.update_session = update
        self.show_panel = show_panel
        self.submissions = submissions
        self._answering = PendingCommands()
        self._target_picker = None

    def capture(self, handle=None):
        return ReceiptScope.capture(self.page, self.presentation.displayed_context, handle)

    def current(self, scope):
        return scope.current(self.page, self.presentation.displayed_context,
                             self.api, self.lifecycle.closing)

    def invalidate(self):
        if self._target_picker is not None:
            self._target_picker.reject()

    def reconnect_instance(self):
        if self.page.reviewing is not None or self.api.is_closing(self.page.session):
            return
        if hasattr(self.api, "control"):
            self.ui.notices.notice("实例重连暂不可用", "项目服务的实例重连尚未开放；现有任务保留")
            return
        context = self.presentation.displayed_context
        if (confirm(self.ui.parent, "结束实例并重连",
                    "将结束此 Virtuoso 实例的全部 Silicon Copilot 连接并建立新连接。\n"
                    "正在执行的 SKILL 无法强制中止；请求和迟到结果保留，不会自动重放。\n"
                    "确认已核对现有操作结果后继续？", choice="结束实例并重连")
                and context == self.presentation.displayed_context):
            if self.lifecycle.notice("reconnect", id=context.target_id) is False:
                self.update_host_notices("failed")
                self.ui.notices.error("重连请求未发送",
                    "请关闭 Silicon Copilot 后从 Virtuoso 重新打开。")
                return
            self.ui.status.setText("重连请求已排队，等待 Virtuoso 处理")


    def update_host_notices(self, state):
        text = {
            "failed": ("宿主通知不可用；当前任务保留。请在本窗口核对快速输入，"
                       "关闭后重新打开以恢复通知。"),
            "stalled": "宿主通知暂时延迟；回执与重连请求等待发送，请勿重复提交。",
        }.get(state, "")
        self.ui.host_notice_status.setText(text)
        self.ui.host_notice_status.setVisible(bool(text))



    def run_command(self, name, *args, controller=None, success=None, failure=None,
                    settled=None, receipt_scope=None, **kwargs):
        self.commands.clear_error()
        handle = self.api.handle(controller) if controller is not None else self.page.session
        if hasattr(self.api, "control") and name in {"cancel", "resume", "acknowledge_interrupted"}:
            kwargs["expected_task"] = (self.presentation.state or {}).get("task", {}).get("id", "")
        scope = receipt_scope or self.capture(handle)
        callbacks = dict(success=success, failure=failure, settled=settled, scope=scope)
        try:
            future = self.api.command(handle, name, *args, **kwargs)
        except (ValueError, RuntimeError) as exc:
            self.commands.rejected(exc, **callbacks)
            return None
        return self.commands.watch(future, **callbacks)

    def rename_session(self, session_id, name, *, success=None, failure=None):
        """Rename any selected session without switching to it."""

        self.commands.clear_error()
        scope = self.capture()
        try:
            future = self.api.rename_session(session_id, name)
        except (ValueError, RuntimeError) as exc:
            self.commands.rejected(exc, success=success, failure=failure, scope=scope)
            return None
        return self.commands.watch(future, success=success, failure=failure, scope=scope)

    def delete_session(self, session_id, *, success=None, failure=None, review_version=None):
        """Delete any selected session without switching to it."""

        self.commands.clear_error()
        scope = self.capture()
        try:
            options = {} if review_version is None else {"review_version": review_version}
            future = self.api.delete_session(session_id, **options)
        except (ValueError, RuntimeError) as exc:
            self.commands.rejected(exc, success=success, failure=failure, scope=scope)
            return None
        return self.commands.watch(future, success=success, failure=failure, scope=scope)

    def initialize_context(self):
        self.run_command("initialize_context")

    def queue_input(self, _checked=False):
        self.submit(_checked, queued=True)

    def submit(self, _checked=False, *, queued=False):
        if not self.steering().available():
            return
        if not queued and self.presentation.state and self.open_audit_ids():
            self.show_pending_audit()
            return
        text = self.ui.input.toPlainText().strip()
        attachment = self.ui.input.text_attachment()
        if attachment is not None and not text:
            text = "请阅读随消息附加的完整文本，并据此处理当前任务。"
        if self.ui.input.turn_inputs and not text:
            text = "请分析所附资料。"
        if not text:
            return
        if not queued and self.steering().routes_to_current():
            self.steering().submit(_checked)
            return
        if not queued and self.steering().new_task_waits():
            self.ui.notices.notice("请等待当前任务", WAIT_HINT)
            return

        controller = self.page.session
        key = controller.session_id
        pending_key = (key, controller.runtime_id)
        if not self.submissions.begin(pending_key):
            return
        revision = self.ui.input.draft_revision
        content = self.ui.input.draft_content()
        self.steering().refresh()
        submissions, drafts = self.submissions, self.drafts

        def settled(_result, error):
            submissions.finish(pending_key)
            if error is None:
                drafts.accept(controller, revision)

        def accepted(_result):
            if self.ui.input.draft_revision == revision and self.ui.input.draft_content() == content:
                self.ui.input.clear_submission()
            if self.presentation.state:
                self.update_session(self.presentation.state)
            self.poll()

        def failed(exc):
            if self.presentation.state:
                self.update_session(self.presentation.state)
            from sico.service.service_errors import is_unknown_outcome

            if is_unknown_outcome(exc):
                self.ui.notices.warning("任务接收结果未确认", "草稿保留；请查询原请求：" + str(exc))
            else:
                self.ui.notices.error("任务未接收", str(exc))
            return False

        options = {"context": self.presentation.submission_context}
        if attachment is not None:
            options["attachment_text"] = attachment
        if self.ui.input.turn_inputs:
            options["inputs"] = self.ui.input.draft()[4]
        if self.ui.input.turn_options is not None:
            options["turn_options"] = self.ui.input.draft()[5]
        self.run_command("submit", text, controller=controller, success=accepted,
                         failure=failed, settled=settled, **options)

    def _attachment_pasted(self):
        self.ui.notices.notice("长文本已暂存为附件",
            f"剪贴板内容超过输入限制，已暂存为文本附件；随新任务提交，执行中请{DEFER_HINT}")

    def show_pending_audit(self, _checked=False):
        if self.page.reviewing is not None or self.presentation.state is None:
            return
        keys = self.open_audit_ids()
        if keys:
            self.centers.show_audit(keys[0])
            self.show_panel("right")

    def open_audit_ids(self, state=None):
        """Ids of audits that still hold the task, judged by the live projection.

        A session snapshot freezes the task dict until the task emits again, so
        an answer delivered during a quiet model turn used to leave 发送 stuck
        on 答复问题. Audit rows carry their own workbench events (opened /
        reply_received / resumed) and settle right away, so they decide for
        every id they already know.
        """
        state = self.presentation.state if state is None else state
        state = state or {}
        task_id = state.get("task", {}).get("id", "")
        waiting = list(state.get("task", {}).get("waiting_audits") or [])
        audits = getattr(getattr(self.centers, "index", None), "audits", None) or {}
        open_ids = [
            row_id for row_id, row in audits.items()
            if row.get("task_id") == task_id and row.get("status") in self.OPEN_AUDIT_STATUSES
        ]
        # An audit the projection has never seen (just opened) still counts.
        open_ids += [key for key in waiting if key not in audits]
        return open_ids

    def target_family(self):
        """Editor family the picker offers; the circuit flow works on schematics."""
        return SCHEMATIC_FAMILY

    def target_picker_provider(self, controller=None, *, task_id="", audit_id=""):
        """Bind an asynchronous picker source to the originating session and task."""
        return self.api.target_source(self.api.handle(controller or self.page.session),
                                      task_id=task_id, audit_id=audit_id)

    def pick_design_target(self, completed, provider=None, *, controller=None,
                           task_id="", audit_id=""):
        """Present the picker without a nested event loop or a synchronous bridge call."""
        if self._target_picker is not None:
            self._target_picker.raise_()
            self._target_picker.activateWindow()
            completed(None)
            return
        controller = self.api.handle(controller or self.page.session)
        scope = self.capture(controller)
        provider = provider or self.target_picker_provider(controller, task_id=task_id,
                                                           audit_id=audit_id)
        if provider is None:
            self.ui.notices.warning("无法打开目标选择器", "当前会话没有桥接连接")
            completed(None)
            return
        picker = TargetPicker(provider, self.target_family(), self.ui.parent,
                              valid=lambda: self.current(scope))
        self._target_picker = picker

        def finished(result):
            self._target_picker = None
            target = picker.selection if result == QDialog.Accepted else None
            picker.deleteLater()
            if self.lifecycle.closing:
                completed(None)
                return
            if self.current(scope):
                if target:
                    self.ui.notices.info("设计目标已绑定", target["library"]
                                        + " / " + target["cell"] + " / " + target["view"])
                    self.poll()

            completed(target if self.current(scope) else None)

        picker.finished.connect(finished)
        picker.open()

    def resolve_target_choice(self, answers, completed, **scope):
        """Resolve target choices asynchronously, leaving the audit draft untouched."""
        answers = {key: dict(answer) for key, answer in answers.items()}
        pending = [answer for answer in answers.values()
                   if str(answer.get("choice", "")).startswith(NEW_WINDOW_OPTION.rstrip("…"))]

        def selected(target):
            if target is None:
                completed(None)
                return
            answer = pending.pop(0)
            label = target["library"] + " / " + target["cell"] + " / " + target["view"]
            note = "已在 Virtuoso 打开并绑定 " + label
            if target.get("window"):
                note += "（窗口 " + str(target["window"]) + "）"
            answer["text"] = (str(answer.get("text", "")).strip() + " " + note).strip()
            advance()

        def advance():
            if pending:
                self.pick_design_target(selected, **scope)
            else:
                completed(answers)

        advance()

    def answer_audit(self, audit_id, answers):
        if self.page.reviewing is not None or self.presentation.state is None or self.lifecycle.closing:
            return
        controller = self.page.session
        receipt_scope = self.capture(controller)
        task_id = self.presentation.state["task"].get("id", "")
        key = (controller.runtime_id, task_id, audit_id)
        if not self._answering.begin(key):
            return
        answering = self._answering

        def visible():
            return (self.current(receipt_scope) and self.page.reviewing is None
                    and self.presentation.state is not None
                    and self.presentation.state["task"].get("id", "") == task_id)

        def failed(exc):
            if visible():
                self.ui.notices.error("答复未提交", str(exc))
                self.centers.audit.show_error("答复未提交：" + str(exc))
            return False

        def accepted(result):
            if result and visible():
                self.ui.status.setText("已提交答复，正在核验依据并继续任务")
                self.poll()

        def resolved(answers):
            if answers is None or not visible():
                answering.finish(key)
                return
            self.run_command("answer_audit", controller.session_id, task_id,
                             audit_id, answers, uuid.uuid4().hex, controller=controller,
                             success=accepted, failure=failed, receipt_scope=receipt_scope,
                             settled=lambda _result, _error: answering.finish(key))

        self.resolve_target_choice(answers, resolved, controller=controller,
                                   task_id=task_id, audit_id=audit_id)
