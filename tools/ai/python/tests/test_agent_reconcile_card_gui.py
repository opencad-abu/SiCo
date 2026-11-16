"""Exercise the actual Qt card links across live, paused and ended pages."""

import pytest

from contextlib import ExitStack
from types import SimpleNamespace

from agent_gui_wait import activate, settle, wait_until
from agent_replay_fixtures import blocked_append, record_task
from agent_service_business_fixtures import business_service, open_session
from agent_worker_fixtures import heartbeat
from agent_worker_fixtures import window as window
from info_app_fixtures import dismiss_notice, notice_text
from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QTextCursor, QTextDocument
from sico_ui.reconcile_actions import ReconcileActions
from sico_ui.reconcile_card import actions_html, card_actions
from sico_ui.session_binding import SessionBinding
from test_agent_interrupted_reconcile import interrupt, status_tool
from test_agent_service_control import attach
from test_agent_service_control_gui import window_for


@pytest.fixture(autouse=True)
def evidence_dialog(monkeypatch):
    monkeypatch.setattr("sico_ui.reconcile_actions.decision_evidence",
                        lambda *_args: "checked original receipt")


class _Notices:
    def __init__(self):
        self.items = []

    def info(self, title, text):
        self.items.append(("info", title, text))

    def warning(self, title, text):
        self.items.append(("warning", title, text))


def reconcile_actions(*, reviewing=None, task=None, rows=None, session_id="s1",
                      token_for=None, page_scope=None):
    """Unit-level harness: no Qt widgets, only the decision the click makes."""
    calls = {"command": [], "poll": 0}
    ui = SimpleNamespace(notices=_Notices())
    page = SimpleNamespace(reviewing=reviewing,
                           api=SimpleNamespace(is_closing=lambda _token: False),
                           session=SimpleNamespace(session_id=session_id, runtime_id="runtime"))
    presentation = SimpleNamespace(state={"task": task or {}},
                                   transcript=SimpleNamespace(messages=rows or []))
    actions = ReconcileActions(
        ui, page=page, presentation=presentation,
        command=lambda *args, **kwargs: calls["command"].append((args, kwargs)),
        poll=lambda: calls.__setitem__("poll", calls["poll"] + 1),
        token_for=token_for,
        page_scope=page_scope,
    )
    return actions, ui, calls


def reconcile_row(task_id="t1", session_id="s1", operations=()):
    return {"role": "reconcile", "text": "", "result": {
        "session_id": session_id, "task_id": task_id, "active": True,
        "message": "", "operations": list(operations),
    }}


def rendered_anchors(html):
    """Read back the anchor hrefs Qt stores for card HTML, as a click would."""
    document = QTextDocument()
    cursor = QTextCursor(document)
    table = cursor.insertTable(1, 1)
    table.cellAt(0, 0).firstCursorPosition().insertHtml(html)
    found = {}
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and fragment.charFormat().isAnchor():
                found[fragment.text()] = fragment.charFormat().anchorHref()
            iterator += 1
        block = block.next()
    return found


def test_reconcile_click_in_history_browsing_explains_read_only():
    actions, ui, calls = reconcile_actions(
        reviewing=object(),
        task={"id": "t1", "status": "needs_reconcile"},
        rows=[reconcile_row()],
    )
    actions.open("reconcile:?session=s1&task=t1&action=no_calls&call=")
    assert ui.notices.items == [(
        "info", "核对操作未执行",
        "当前正在浏览历史（只读）；点击“继续”回到当前会话后再核对原中断任务",
    )]
    assert not calls["command"] and calls["poll"] == 0


def test_reconcile_click_dispatches_for_the_service_to_authorize():
    """核对身份由服务端权威校验；本地状态陈旧不再形成点击死循环。"""
    row = reconcile_row()
    actions, ui, calls = reconcile_actions(
        task={"id": "t1", "status": "cancelled"},
        rows=[row],
    )
    url = dict(card_actions(row["result"]))["结束中断任务并继续（不重放）"]
    actions.open(url)
    assert not ui.notices.items
    (args, kwargs), = calls["command"]
    assert args == ("resolve_interrupted", "", "no_calls")
    assert kwargs["expected_task"] == "t1"
    assert callable(kwargs["failure"])
    assert "controller" not in kwargs


def test_reconcile_refusal_surfaces_the_service_error_without_a_loop():
    row = reconcile_row()
    actions, ui, calls = reconcile_actions(
        task={"id": "t1", "status": "needs_reconcile"},
        rows=[row],
    )
    url = dict(card_actions(row["result"]))["结束中断任务并继续（不重放）"]
    actions.open(url)
    (_, kwargs), = calls["command"]
    assert kwargs["failure"](ValueError("原任务已变化，操作未执行")) is False
    assert calls["poll"] == 1
    assert ui.notices.items[-1] == ("warning", "核对操作未执行", "原任务已变化，操作未执行")


def test_foreign_reconcile_link_routes_to_the_cards_own_session():
    """卡片属于其他会话时，动作路由到卡片自身会话，而不是永久拒绝。"""
    row = reconcile_row(session_id="other")
    actions, ui, calls = reconcile_actions(
        task={"id": "t1", "status": "needs_reconcile"},
        rows=[row],
        token_for=lambda session_id: "token:" + session_id,
        page_scope=lambda: "scope:page",
    )
    url = dict(card_actions(row["result"]))["结束中断任务并继续（不重放）"]
    actions.open(url)
    assert not ui.notices.items
    (args, kwargs), = calls["command"]
    assert args == ("resolve_interrupted", "", "no_calls")
    assert kwargs["expected_task"] == "t1"
    assert kwargs["controller"] == "token:other"
    assert kwargs["receipt_scope"] == "scope:page"


def test_foreign_reconcile_link_without_a_token_guides_to_that_session():
    row = reconcile_row(session_id="other")
    actions, ui, calls = reconcile_actions(
        task={"id": "t1", "status": "needs_reconcile"},
        rows=[row],
    )
    url = dict(card_actions(row["result"]))["结束中断任务并继续（不重放）"]
    actions.open(url)
    assert calls["poll"] == 1
    assert not calls["command"]
    assert ui.notices.items[0][0] == "warning"
    assert "请在左侧打开该会话后再核对" in ui.notices.items[0][2]


def test_reconcile_click_with_a_current_card_issues_the_exact_command():
    row = reconcile_row(operations=[{"id": "call-1", "name": "execute_circuit_operation"}])
    actions, ui, calls = reconcile_actions(
        task={"id": "t1", "status": "needs_reconcile"},
        rows=[row],
    )
    url = dict(card_actions(row["result"]))["1：已完成，继续"]
    actions.open(url)
    assert not ui.notices.items
    (args, kwargs), = calls["command"]
    assert args == ("resolve_interrupted", "call-1", "completed")
    assert kwargs["expected_task"] == "t1"


def test_card_link_survives_the_qt_html_round_trip():
    """Qt 回传卡片链接时可能保留 HTML 实体；点击仍须落到卡片自身的命令。"""
    row = reconcile_row(operations=[{"id": "call-1", "name": "execute_circuit_operation"}])
    anchors = rendered_anchors(actions_html(row["result"], read_only=False))
    actions, ui, calls = reconcile_actions(
        task={"id": "t1", "status": "needs_reconcile"},
        rows=[row],
    )
    actions.open(anchors["1：已完成，继续"])
    assert not ui.notices.items, ui.notices.items
    (args, kwargs), = calls["command"]
    assert args == ("resolve_interrupted", "call-1", "completed")
    assert kwargs["expected_task"] == "t1"


def test_card_click_queries_then_continues_and_rejects_the_old_link(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        controller = service.owner.controllers["live"]
        task = interrupt(controller, calls=1)
        observed = []
        status_tool(controller, observed)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        window = window_for(stack, api, token)
        window.show()
        wait_until(lambda: window.reconcile_panel.isVisible())
        row = next(row for row in window.presentation.transcript.messages if row["role"] == "reconcile")
        actions = dict(card_actions(row["result"]))
        query = next(url for label, url in actions.items() if "查询这次请求" in label)
        complete = next(url for label, url in actions.items() if "已完成" in label)
        uncertain = actions["仍不确定，保持暂停"]
        window.reconcile_panel.buttons["uncertain"].click()
        wait_until(lambda: "保持暂停" in notice_text(window))
        assert controller.paused and "保持暂停" in notice_text(window)
        dismiss_notice(window)
        window.reconcile_panel.buttons["query"].click()
        wait_until(lambda: len(observed) == 1)
        wait_until(lambda: "原请求核对" in notice_text(window))
        assert controller._task["id"] == task and controller.paused
        dismiss_notice(window)
        window.reconcile_panel.buttons["completed"].click()
        wait_until(lambda: not controller.paused)
        wait_until(lambda: window.presentation.state["task"]["status"] == "cancelled")
        wait_until(lambda: "核对决定已记录" in notice_text(window))
        dismiss_notice(window)
        window.display.anchorClicked.emit(QUrl(query))
        # 拒绝由服务端权威判定并异步返回；页面刷新后用明确原因收尾，
        # 不再依赖本地状态在点击瞬间恰好匹配。
        wait_until(lambda: "原中断任务已变化" in notice_text(window))
        assert len(observed) == 1
        assert not service.provider.seen


def test_close_window_and_service_after_interrupted_session_is_already_archived(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        controller = service.owner.controllers["live"]
        interrupt(controller, calls=1)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, "acquire").result(5)
        window = window_for(stack, api, token)
        window.show()
        window.service_connection.ending.request(token)
        wait_until(lambda: not service.owner.controllers)
        wait_until(lambda: not window.service_connection.ending.pending)
        assert not notice_text(window)
        requested = []
        monkeypatch.setattr(window.close_choice, "_finished", lambda: requested.append(True))
        window.close_choice.stop_everything()
        wait_until(lambda: bool(requested))
        assert not window.close_choice.busy


def test_panel_tolerates_a_page_whose_replay_is_still_pending(window, monkeypatch):
    """A pending history preview has no replay yet; the panel tick must not abort the window."""
    activate(window, "history")
    history = window.controller
    record_task(history.journal, history.current, "old", native=True)
    monkeypatch.setattr(SessionBinding, "REPLAY_MESSAGES", 1)
    activate(window, "initial")
    panel = window.reconcile_panel
    with blocked_append(monkeypatch, history):
        window.page.preview_session("history")
        heartbeat(window)
        assert window.page.reviewing == "history" and window.page.opening
        assert window.presentation.current_transcript(window.page.reviewing) is None
        card = panel.card
        panel.refresh()  # 修复前这里抛 AttributeError，窗口进程随即被 PyQt abort
        assert panel.card is card
    settle(window)
    assert window.presentation.current_transcript(window.page.reviewing) is not None
    panel.refresh()
