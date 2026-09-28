"""Route card decisions to the captured task without submitting another task."""

from urllib.parse import parse_qs, urlsplit

from .reconcile_card import card_actions


class ReconcileActions:
    def __init__(self, ui, *, page, presentation, command, poll):
        self.ui, self.page, self.presentation = ui, page, presentation
        self.command, self.poll = command, poll

    def open(self, value):
        if self.page.reviewing is not None:
            return
        task = (self.presentation.state or {}).get("task", {})
        rows = self.presentation.transcript.messages
        allowed = {url for row in rows if row["role"] == "reconcile"
                   and row["result"].get("active")
                   and row["result"]["task_id"] == task.get("id")
                   and row["result"]["session_id"] == self.page.session.session_id
                   for _, url in card_actions(row["result"])}
        if value not in allowed or task.get("status") != "needs_reconcile":
            self.ui.notices.warning("核对操作未执行", "原中断任务已变化，请刷新后核对")
            return
        params = {key: values[0] for key, values in
                  parse_qs(urlsplit(value).query, keep_blank_values=True).items()}
        action = params["action"]
        if action == "uncertain":
            self.ui.notices.info("会话保持暂停", "结果仍不确定，会话保持暂停；原调用不会重发")
            return
        options = dict(expected_task=params["task"], success=lambda _result: self.poll())
        if action == "query":
            self.command("query_interrupted", params["call"], **options)
        else:
            self.command("resolve_interrupted", params["call"], action, **options)
