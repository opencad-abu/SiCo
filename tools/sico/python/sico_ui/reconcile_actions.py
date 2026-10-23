"""Route card decisions to the captured task without submitting another task."""

from html import unescape
from urllib.parse import parse_qs, urlsplit

from PyQt5.QtWidgets import QInputDialog

ACTIONS = {"query", "completed", "not_executed", "no_calls", "uncertain"}


def default_evidence(decision, operation=None):
    """Compose a reviewable draft from the recorded call; the user may edit it."""
    operation = operation or {}
    name = str(operation.get("name") or "工具")
    call_id = str(operation.get("id") or "")
    result = operation.get("result") or {}
    status = str(result.get("status") or "")
    summary = str(result.get("summary") or "")
    request_id = str(operation.get("request_id") or "")
    target = operation.get("target")
    label = name + ("（" + call_id + "）" if call_id else "")
    if decision == "completed":
        head = "按调用记录核对：" + label + "已执行"
    else:
        head = "按调用记录核对：" + label + "未执行或被放弃；不重放该请求"
    parts = [head]
    if status or summary:
        parts.append("结果：" + status + (" — " + summary if summary else ""))
    else:
        parts.append("结果见会话记录")
    if request_id:
        parts.append("请求号：" + request_id)
    if isinstance(target, (list, tuple)) and target:
        parts.append("目标：" + "/".join(str(item) for item in target))
    return ("；".join(parts) + "。")[:3980]


def decision_evidence(parent, decision, operation=None):
    label = "已完成" if decision == "completed" else "未执行"
    text, accepted = QInputDialog.getMultiLineText(parent, "确认核对结论：" + label,
        "已按调用记录生成核对依据草稿：确认无误请直接点“确定”，需要补充时直接编辑。\n"
        "已完成表示结果已核对；若操作失败或被停止，请如实说明已发生的效果。\n"
        "此决定将记入会话记录；全部调用核对后结束本轮，原调用不会重放。",
        default_evidence(decision, operation))
    return text.strip() if accepted else None


class ReconcileActions:
    def __init__(self, ui, *, page, presentation, command, poll, token_for=None,
                 page_scope=None):
        self.ui, self.page, self.presentation = ui, page, presentation
        self.command, self.poll = command, poll
        self.token_for = token_for
        self.page_scope = page_scope

    @property
    def read_only(self):
        return (self.page.reviewing is not None or self.page.session.runtime_id is None
                or self.page.api.is_closing(self.page.session))

    def _refused(self, error):
        """The service owns the authoritative reconciliation identity.

        A rejected decision refreshes the page and explains the exact reason;
        the card never becomes a click loop that only repeats a local warning.
        """
        self.poll()
        self.ui.notices.warning("核对操作未执行", str(error))
        return False

    def open(self, value):
        if self.read_only:
            self.ui.notices.info(
                "核对操作未执行",
                "当前正在浏览历史（只读）；点击“继续”回到当前会话后再核对原中断任务",
            )
            return
        # Qt 回传的 anchor href 可能原样带着 HTML 实体（单引号属性不展开
        # ``&amp;``），先还原再解析，否则每张卡片的链接都会被判为无效。
        value = unescape(value)
        if not value.startswith("reconcile:?"):
            return
        params = {key: values[0] for key, values in
                  parse_qs(urlsplit(value).query, keep_blank_values=True).items()}
        if (set(params) != {"session", "task", "action", "call"}
                or params["action"] not in ACTIONS):
            # 形状不符（旧版界面残留或链接被改写）时先刷新，再明确告诉用户
            # 下一步该做什么，而不是只丢一句“无效”。
            self.poll()
            self.ui.notices.warning(
                "核对操作未执行",
                "该链接不是当前版本的核对动作（可能来自旧界面或已被改写）；"
                "已刷新会话。请点击卡片上重新显示的按钮重试；"
                "若卡片已消失，说明该中断已处理，可直接发送新任务。",
            )
            return
        self.decide(params["session"], params["task"], params["action"], params["call"])

    def decide(self, session_id, task_id, action, call_id="", operation=None):
        """Real buttons call this typed route; HTML links are only a compatibility input."""
        if self.read_only:
            self.ui.notices.info("核对操作未执行", "当前是历史只读；点击左侧会话的“继续”后核对，待核对记录会保留")
            return
        if getattr(getattr(self.page, "api", None), "connection_state", "connected") != "connected":
            self.ui.notices.warning("核对操作未执行", "项目服务未连接；请使用“重新连接项目服务”，或使用 sico reconcile 命令核对")
            return
        # 核对卡可能来自左侧列表中的其他会话；动作属于卡片自身的会话，
        # 不能因为当前视图不同就把链接永久拒绝。
        controller = None
        if session_id != self.page.session.session_id:
            controller = self.token_for(session_id) if self.token_for else None
            if controller is None:
                self.poll()
                self.ui.notices.warning(
                    "核对操作未执行",
                    "该核对卡属于会话 " + session_id[:12]
                    + "…；请在左侧打开该会话后再核对",
                )
                return
        options = dict(expected_task=task_id, success=self._accepted,
                       failure=self._refused)
        if controller is not None:
            options["controller"] = controller
            # 外来会话的命令不在当前页句柄下；显式携带本页作用域，
            # 否则回执回调会被静默丢弃，失败也无声。
            if self.page_scope is not None:
                options["receipt_scope"] = self.page_scope()
        if action == "query":
            self.command("query_interrupted", call_id, **options)
        else:
            if action in {"completed", "not_executed"}:
                evidence = decision_evidence(getattr(self.ui, "parent", None), action,
                                             operation)
                if evidence is None:
                    return
                if not evidence:
                    self.ui.notices.warning("核对决定未提交", "请填写核对依据；仍不确定时选择“保持暂停”")
                    return
                options["evidence"] = evidence
            self.command("resolve_interrupted", call_id, action, **options)

    def _accepted(self, result):
        self.poll()
        if result and result.get("message"):
            self.ui.notices.info("核对结果", result["message"])
