"""Action links for a journal-derived reconciliation card."""

from html import escape
from urllib.parse import urlencode


def card_actions(result):
    base = dict(session=result["session_id"], task=result["task_id"])
    def link(action, call_id=""):
        return "reconcile:?" + urlencode(dict(base, action=action, call=call_id))
    rows = result["operations"]
    if not rows:
        return [("结束中断任务并继续（不重放）", link("no_calls"))]
    actions = []
    for index, row in enumerate(rows, 1):
        label = str(index)
        actions.append((label + "：查询这次请求的现状", link("query", row["id"])))
        if not row.get("decision"):
            actions.extend((
                (label + "：已完成，继续", link("completed", row["id"])),
                (label + "：未执行，放弃本轮", link("not_executed", row["id"])),
            ))
    actions.append(("仍不确定，保持暂停", link("uncertain")))
    return actions


def actions_html(result, *, read_only):
    if read_only or not result.get("active"):
        return "<p>核对记录（只读）</p>"
    return "<br>".join("<a href='" + escape(url, quote=True) + "'>" + escape(label) + "</a>"
                          for label, url in card_actions(result))
