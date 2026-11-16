"""The status bar carries state/progress; notices retain an explicit detail destination."""

# Pytest discovers the imported fixtures by name.
# ruff: noqa: F811

import pytest
from agent_navigation_contract_support import app, window  # noqa: F401
from agent_service_gui_fixtures import remote_window
from info_app_fixtures import dismiss_notice, notice_text
from PyQt5.QtWidgets import QDialog, QPushButton
from sico.service.published import thaw
from sico_ui.service_connection import attach_service_connection
from sico_ui.tasks import session_status


def state(**changes):
    value = dict(fault="", closing=False, cancelling=False, busy=False,
                 paused=False, pending=0, task={}, router={})
    value.update(changes)
    return value


@pytest.mark.parametrize("snapshot,expected", [
    (state(fault="private exception\nlong recovery instructions"), "会话故障"),
    (state(task=dict(status="needs_reconcile", diagnostic="raw interruption reason")),
     "任务已中断 · 待核对"),
    (state(closing=True, busy=True), "正在结束会话…"),
    (state(cancelling=True), "正在取消…"),
    (state(router=dict(state="blocked_unknown")), "Virtuoso 执行结果待确认"),
    (state(router=dict(state="router_unavailable")), "Virtuoso 未连接"),
])
def test_status_summarizes_failures_without_printing_details(snapshot, expected):
    assert session_status(snapshot) == expected


@pytest.mark.parametrize("activity,expected", [
    (dict(label="工具执行中", tool="get_circuit_operation", turn=2),
     "工具执行中：get_circuit_operation（第 2 轮）；队列 3 条"),
    (dict(label="模型思考中", turn=4), "模型思考中（第 4 轮）；队列 3 条"),
    (dict(label="命令执行中"), "命令执行中；队列 3 条"),
])
def test_conversation_activity_still_has_tool_model_command_and_queue(activity, expected):
    assert session_status(state(busy=True, pending=3, task=dict(activity=activity))) == expected


def test_fault_details_open_in_info_app_and_clear_after_recovery(window):
    snapshot = thaw(window.presentation.state)
    snapshot["fault"] = "disk full\n保留完整错误详情 <plain text>"
    window.renderer.update_session(snapshot)
    assert window.status.text() == "会话故障"
    assert not notice_text(window)
    panel = window.session_notices
    panel.findChild(QPushButton).click()
    assert "错误 · 会话故障" in notice_text(window)
    assert snapshot["fault"] in notice_text(window)
    dismiss_notice(window)
    snapshot["fault"] = ""
    window.renderer.update_session(snapshot)
    assert not panel.messages and panel.isHidden()


def test_background_catalog_failure_keeps_progress_and_opens_details_only_on_click(
        window, monkeypatch):
    panel = window.navigation.notices
    before = window.status.text()
    original = window.navigation._refresh

    def broken():
        raise ValueError("catalog unavailable\nfull diagnostic")

    monkeypatch.setattr(window.navigation, "_refresh", broken)
    for _ in range(30):
        window.navigation.refresh()
    assert window.status.text() == before
    assert not notice_text(window)
    assert len(panel.findChildren(QPushButton)) == 1
    panel.findChild(QPushButton).click()
    assert "会话历史暂不可用" in notice_text(window)
    assert "catalog unavailable\nfull diagnostic" in notice_text(window)
    dismiss_notice(window)
    monkeypatch.setattr(window.navigation, "_refresh", original)
    window.navigation.refresh()
    assert not panel.messages and panel.isHidden()


def test_reconnect_exception_is_passive_for_automatic_retry_and_popup_for_user(
        tmp_path, monkeypatch, app):
    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        connection = window.service_connection
        connection.timer.stop()
        before = window.status.text()

        def broken(*_args):
            raise OSError("socket failure\ncomplete diagnostic")

        monkeypatch.setattr(window.api, "reconnect_session", broken)
        for _ in range(10):
            connection.reconnect(automatic=True)
        assert window.status.text() == before
        assert connection.label.text() == "会话连接未恢复"
        assert not notice_text(window)
        assert "socket failure" in window.session_notices.messages["connection"][1]
        connection.reconnect()
        assert "警告 · 会话连接未恢复" in notice_text(window)
        assert "socket failure\ncomplete diagnostic" in notice_text(window)


def test_control_retry_keeps_error_out_of_status_and_clears_it_when_writable(
        tmp_path, monkeypatch, app):
    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        control = window.service_connection.control
        control.timer.stop()

        def broken(*_args):
            raise ValueError("authority receipt unavailable")

        monkeypatch.setattr(window.api.control, "ensure", broken)
        for _ in range(5):
            control.request("acquire")
        assert "authority receipt" not in control.label.text()
        assert not notice_text(window)
        assert "authority receipt" in window.session_notices.messages["control"][1]
        control.refresh()
        assert "control" not in window.session_notices.messages


def test_paused_queue_does_not_hide_live_tool_progress(window):
    snapshot = thaw(window.presentation.state)
    snapshot.update(paused=True, pending=2, busy=True)
    snapshot["task"].update(status="running", activity=dict(label="工具执行中", tool="read"))
    window.renderer.update_session(snapshot)
    assert window.status.text() == "工具执行中：read；队列 2 条"
    assert "仍在执行" in window.resume.toolTip()


@pytest.mark.parametrize("accepted", [True, False])
def test_target_picker_result_uses_info_app_and_cancel_leaves_status_alone(
        window, monkeypatch, accepted):
    class Picker(QDialog):
        def __init__(self, _provider, _family, parent, **_kwargs):
            super().__init__(parent)
            self.selection = dict(library="work", cell="inv", view="schematic")

    monkeypatch.setattr("sico_ui.window_actions.TargetPicker", Picker)
    monkeypatch.setattr(window.actions, "poll", lambda: None)
    before = window.status.text()
    results = []
    window.actions.pick_design_target(results.append, provider=object())
    window.actions._target_picker.done(QDialog.Accepted if accepted else QDialog.Rejected)
    assert window.status.text() == before
    if accepted:
        assert "信息 · 设计目标已绑定" in notice_text(window)
        assert "work / inv / schematic" in notice_text(window)
        assert results[0]["cell"] == "inv"
    else:
        assert results == [None] and not notice_text(window)


def test_layout_save_failure_does_not_overwrite_progress_or_block_hiding(window, monkeypatch):
    window.lifecycle.persistent = True
    before = window.status.text()

    def broken():
        raise OSError("layout directory is read-only")

    with monkeypatch.context() as failed:
        failed.setattr(window, "save_layout", broken)
        window.close()
    assert not window.isVisible() and not window.lifecycle.closing
    assert window.status.text() == before and not notice_text(window)
    assert "layout directory is read-only" in window.session_notices.messages["layout"][1]
    window.close()
    assert "layout" not in window.session_notices.messages
