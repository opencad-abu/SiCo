# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent navigation contract availability contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import threading

import pytest
from agent_gui_wait import settle, wait_until
from agent_navigation_contract_support import (
    BlockingProvider,
    DeletableLoop,
    _alert_texts,
    _browse,
    _counts,
    _dot_color,
    _item,
    _tick,
    _tree_of,
)
from agent_window_fixture import create_window as AssistantWindow
from info_app_fixtures import dismiss_notice, notice_text
from PyQt5.QtCore import QEvent, Qt
import sico_ui.navigation as navigation_module
from sico.core.contracts import BoundContext
from sico.core.tools import ToolRegistry
from sico.storage.journal import SessionJournal
from sico_ui.theme import active_palette
from sico_ui.window import STOP_FIRST_DELETE


def test_status_column_tracks_active_pending_and_free(tmp_path, app):
    entered, release = threading.Event(), threading.Event()
    context = BoundContext("instance", "generation", "source", {"valid": True})
    with SessionJournal(tmp_path, "initial") as journal:
        loop = DeletableLoop(BlockingProvider(entered, release), ToolRegistry(), journal, context)
        loop._event("session.created", {})
        window = AssistantWindow(loop)
        window.navigation.timer.stop()
        try:
            _tick(window)
            item = _item(window, "initial")
            assert item.toolTip(0).startswith("空闲\ninitial")
            assert not item.icon(0).isNull()
            assert _dot_color(item) == navigation_module.ACTIVITY["free"][1].getRgb()[:3]

            window.input.setPlainText("跑一个任务")
            window.actions.submit()
            settle(window, tasks=False)
            assert entered.wait(5)
            _tick(window)
            assert _item(window, "initial").toolTip(0).startswith("活跃\ninitial")
            active_dot = navigation_module.ACTIVITY["active"][1].getRgb()[:3]
            assert _dot_color(_item(window, "initial")) == active_dot

            window.renderer.cancel_task()
            settle(window, tasks=False)
            release.set()
            assert window.controller.wait(5)
            _tick(window)
            assert _item(window, "initial").toolTip(0).startswith("挂起\ninitial")
            pending_dot = navigation_module.ACTIVITY["pending"][1].getRgb()[:3]
            assert _dot_color(_item(window, "initial")) == pending_dot
            assert window.controller.activity() == "pending"

            wait_until(lambda: not window.presentation.state.get("cancelling")
                       and window.send.isEnabled())
            window.input.setPlainText("再跑一个任务")
            window.actions.submit()
            settle(window, tasks=False)
            assert window.controller.wait(5)
            _tick(window)
            assert _item(window, "initial").toolTip(0).startswith("空闲\ninitial")
            assert window.controller.activity() == "idle"
        finally:
            release.set()
            window.close()
            window.sessions.wait(5)
            window.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()


def test_active_session_delete_asks_to_stop_first(tmp_path, app, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    context = BoundContext("instance", "generation", "source", {"valid": True})
    with SessionJournal(tmp_path, "initial") as journal:
        loop = DeletableLoop(BlockingProvider(entered, release), ToolRegistry(), journal, context)
        loop._event("session.created", {})
        window = AssistantWindow(loop)
        settle(window)
        window.navigation.timer.stop()
        try:
            window.input.setPlainText("跑一个任务")
            window.actions.submit()
            settle(window, tasks=False)
            assert entered.wait(5)
            _tick(window)

            window.navigation._delete("initial")
            assert STOP_FIRST_DELETE in _alert_texts(window)
            assert not window.sessions.controllers["initial"].closing
        finally:
            release.set()
            window.controller.wait(5)
            window.close()
            window.sessions.wait(5)
            window.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()


def test_unavailable_session_is_gray_unselectable_and_reports_once(window, monkeypatch):
    initial = window.controller
    initial.delete()
    _tick(window)
    item = _item(window, "initial")
    assert item.data(0, Qt.UserRole + 2) == "已删除"

    # 双保险一：不可用的会话灰掉且不可选中，点击不再触发任何操作。
    assert not item.flags() & Qt.ItemIsSelectable
    assert not item.flags() & Qt.ItemIsEnabled
    assert item.foreground(0).color().name() == active_palette().muted.lower()
    assert "已删除" in item.text(0) and "已删除" in item.toolTip(0)
    # 不可用行不是"空闲"：圆点回到灰色，和灰掉的文字一致。
    assert _dot_color(item) == navigation_module.ACTIVITY["idle"][1].getRgb()[:3]

    # 后台刷新保留状态提示，不创建会抢焦点的顶层对话框。
    assert "当前会话已删除" not in window.status.text()
    assert any("当前会话已删除" in text for text in _alert_texts(window))
    assert not notice_text(window)

    # 即使信号被直接触发（键盘/程序调用），也会弹窗说明原因。
    window.navigation.select(item)
    assert any("此会话已删除" in text for text in _alert_texts(window))

    # 右键同样的行给出同样的弹窗，而不是静默返回。
    monkeypatch.setattr(
        window.navigation.list, "itemAt", lambda position: item
    )
    window.navigation._menu(window.navigation.list.viewport().rect().center(),
                            window.navigation.list)
    assert any("此会话已删除" in text for text in _alert_texts(window))

    # 返回现场失败同样弹窗。
    dismiss_notice(window)
    window.navigation.return_live()
    assert any("初始会话已删除或不可用" in text for text in _alert_texts(window))


def test_unavailable_refresh_never_maps_a_dialog(window):
    """Background selection must not synthesize the explicit-click warning."""

    window.controller.delete()
    _tick(window)
    assert not notice_text(window)
    for _ in range(5):
        window.navigation.refresh()
    assert not notice_text(window)
    assert "initial" in window.navigation.notices.messages


def test_deleted_session_row_is_removed_from_navigation(window, monkeypatch):
    """双保险二：记录已经不存在的会话直接从导航移除，不再留给用户点击。"""

    monkeypatch.setattr("sico_ui.navigation_actions.confirm",
                        lambda *args, **kwargs: True)
    # 历史会话从未在本窗口打开；单击选中后删除，其行必须消失。
    window.navigation.list.setCurrentItem(_item(window, "history"))
    settle(window, tasks=False)
    window.navigation._delete("history")
    settle(window)
    assert _counts(window) == (1, 0)
    assert window.page.reviewing is None
    # 选中的行没了：高亮回落到现场会话。
    assert window.navigation.current.currentItem().data(0, Qt.UserRole) == "initial"
    # 现场会话被删除时保留行以说明原因，但同样不可选中。
    window.actions.delete_session("initial")
    settle(window, tasks=False)
    _tick(window)
    item = _item(window, "initial")
    assert item.data(0, Qt.UserRole + 2) == "已删除"
    assert not item.flags() & Qt.ItemIsSelectable


def test_clicking_deleted_session_keeps_session_actions_usable(window):
    """点击已删除会话后，新建与切换会话必须仍然可用（不得卡住主窗口）。"""

    initial = window.controller
    initial.delete()
    _tick(window)
    window.navigation.select(_item(window, "initial"))
    assert window.navigation.list.isEnabled()  # 列表仍可交互，没有嵌套事件循环

    window.navigation._continue_session("history")
    settle(window)
    assert window.controller.session_id == "history"
    assert window.input.isEnabled()

    window.new_session()
    settle(window)
    assert window.controller.session_id not in {"initial", "history"}
    assert not window.sessions.worker.busy
    assert not window.page.opening


def test_vanished_browsed_session_returns_to_live_view(window):
    """浏览中的记录从磁盘消失时，结束浏览并回到现场，而不是留下死视图。"""

    _browse(window, "history")
    assert window.page.reviewing == "history"

    shutil.rmtree(window.sessions.root / "sessions" / "history")
    _tick(window)

    settle(window, tasks=False)
    assert window.page.reviewing is None
    assert window.controller.session_id == "initial"
    assert window.input.isEnabled()
    assert window.navigation.current.currentItem().data(0, Qt.UserRole) == "initial"
    assert any("正在浏览的会话" in text for text in _alert_texts(window))


def test_last_usable_session_cannot_be_deleted(window, monkeypatch):
    monkeypatch.setattr("sico_ui.navigation_actions.confirm",
                        lambda *args, **kwargs: True)
    shutil.rmtree(window.sessions.root / "sessions" / "history")
    _tick(window)
    assert _counts(window) == (1, 0)
    assert window.controller.session_id == "initial"

    window.navigation._delete("initial")
    assert any("唯一的可用会话" in text for text in _alert_texts(window))
    assert not window.sessions.controllers["initial"].closing
    assert window.input.isEnabled()

    # 有第二个可用会话后，删除当前会话仍然允许，并回落到启动会话。
    window.new_session()
    settle(window)
    created = window.controller.session_id
    assert created not in {"initial", "history"}
    # 历史记录已被删除：两块区域分别只剩两个已打开会话与空历史。
    assert _counts(window) == (2, 0)
    window.navigation._delete(created)
    settle(window)
    assert window.controller.session_id == "initial"
    assert window.input.isEnabled()


def test_closing_live_session_reports_the_session_ended(window, monkeypatch):
    """An ended session must not be described as deleted or unreadable; it archives."""

    window.controller.closing = True
    _tick(window)
    assert "请切换" not in window.status.text()
    assert any("当前会话已结束" in text for text in _alert_texts(window))
    # 结束的会话归档到下块，圆点静默，记录仍可选中浏览。
    item = _item(window, "initial")
    assert _tree_of(window, "initial") is window.navigation.list
    assert not item.data(0, Qt.UserRole + 2)
    assert item.flags() & Qt.ItemIsEnabled and item.flags() & Qt.ItemIsSelectable
    assert "（已结束）" in item.text(0) and "当前会话" not in item.text(0)
    assert _dot_color(item) == navigation_module.ACTIVITY["idle"][1].getRgb()[:3]
    assert _counts(window) == (0, 2)


def test_live_unavailable_text_names_the_reason_and_detail():
    from sico_ui.navigation import Navigation

    ended = Navigation._unavailable_live_text({}, ended=True)
    assert ended.startswith("当前会话已结束") and "已删除" not in ended
    detail = Navigation._unavailable_live_text(
        {"unavailable": "记录不可用", "unavailable_detail": "ValueError: 会话日志身份或顺序不匹配"}
    )
    assert detail.startswith("当前会话记录暂不可读") and "已删除" not in detail
    assert "ValueError" in detail
    plain = Navigation._unavailable_live_text({"unavailable": "已删除"})
    assert plain == "当前会话已删除，请切换会话或新建会话。"


@pytest.mark.parametrize("failed_stream", [False, True])
def test_catalog_recovery_restores_input_only_for_a_healthy_live_stream(window, monkeypatch,
                                                                      failed_stream):
    catalog = window.sessions.catalog
    healthy = catalog.snapshot()
    unavailable = dict(healthy, version=healthy["version"] + 1,
                       rows=tuple(row for row in healthy["rows"] if row["id"] != "initial"),
                       retained={"initial": {"id": "initial", "unavailable": "记录不可用"}})
    monkeypatch.setattr(catalog, "snapshot", lambda *_: unavailable)
    window.input.setPlainText("保留草稿")
    window.navigation.refresh()
    assert not window.input.isEnabled() and not window.send.isEnabled()
    assert not window.controller.closing
    window.binding.failed = failed_stream
    monkeypatch.setattr(catalog, "snapshot", lambda *_: dict(healthy, version=healthy["version"] + 2))
    window.navigation.refresh()
    assert window.input.isEnabled() is not failed_stream
    assert window.send.isEnabled() is not failed_stream
    assert window.input.toPlainText() == "保留草稿"
    assert not notice_text(window)


def test_hidden_window_catalog_failures_never_map_or_recreate_dialogs(window, monkeypatch):
    window.hide()
    window.input.setPlainText('preserve draft')
    calls = []
    monkeypatch.setattr(window.info_app, 'warning', lambda *_: calls.append('dialog'))
    monkeypatch.setattr(window, 'activateWindow', lambda: calls.append('activate'))
    healthy = window.sessions.catalog.snapshot()
    unavailable = dict(healthy, version=healthy['version'] + 1,
                       rows=tuple(row for row in healthy['rows'] if row['id'] != 'initial'),
                       retained={'initial': {'id': 'initial', 'unavailable': '记录不可用'}})
    monkeypatch.setattr(window.sessions.catalog, 'snapshot', lambda *_: unavailable)
    for _ in range(30):
        window.navigation.refresh()
    assert not calls and not window.isVisible()
    assert not notice_text(window)
    assert 'initial' in window.navigation.notices.messages
    assert '当前会话' not in window.status.text()
    assert '当前会话' in window.navigation.notices.messages['initial'][1]
    assert window.input.toPlainText() == 'preserve draft'
    monkeypatch.setattr(window.sessions.catalog, 'snapshot',
                        lambda *_: dict(healthy, version=healthy['version'] + 2))
    window.navigation.refresh()
    assert 'initial' not in window.navigation.notices.messages


def test_live_session_left_by_another_window_stays_in_history_until_continued(window):
    """服务里仍活着但不是本窗口打开的会话（上次运行遗留）留在下块，继续后才进上块。"""

    assert _counts(window) == (1, 1)
    window.api.open_session("history").result(5)  # 服务里变活：不是本窗口“打开过”
    _tick(window)
    assert _tree_of(window, "history") is window.navigation.list
    assert _counts(window) == (1, 1)

    window.page.activate_session("history")  # 右键“继续”走的就是这条路径
    settle(window, tasks=False)
    _tick(window)
    assert _tree_of(window, "history") is window.navigation.current
    assert _counts(window) == (2, 0)
