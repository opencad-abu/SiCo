# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent navigation contract recovery contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil

import pytest
from agent_gui_wait import activate, settle
from agent_navigation_contract_support import (
    _alert_texts,
    _browse,
    _counts,
    _item,
    _tick,
    _tree_of,
    _trees,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QBrush
from PyQt5.QtTest import QTest

import sico_ui.navigation as navigation_module
from sico.service.published import thaw


def test_timer_survives_initial_deletion_while_history_is_selected(window, app, tmp_path):
    activate(window, "history")
    window.actions.delete_session("initial")
    settle(window, tasks=False)
    timer = window.navigation.timer
    ticks = []
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(1)
    try:
        QTest.qWait(30)
    finally:
        timer.stop()
    assert ticks
    item = _item(window, "initial")
    assert item.data(0, Qt.UserRole + 2) == "已删除"
    # 本窗口自己的会话即使不可用也留在"当前"，不会掉进历史区。
    assert _tree_of(window, "initial") is window.navigation.current
    assert "已删除" in item.text(0)
    assert item.foreground(0) == QBrush(Qt.gray)
    assert window.controller.session_id == "history"
    assert window.input.isEnabled() and window.send.isEnabled()
    for tree in _trees(window):
        assert not tree.signalsBlocked()
    window.show()
    app.processEvents()
    assert window.grab().save(str(tmp_path / "deleted-initial-history.png"))


def test_delete_initial_disables_input_and_allows_switching_to_history(window, monkeypatch):
    monkeypatch.setattr("sico_ui.navigation_actions.confirm",
                        lambda *args, **kwargs: True)
    window.navigation._delete("initial")
    settle(window)
    assert window.controller.closing
    assert not window.input.isEnabled() and not window.send.isEnabled()
    assert _item(window, "initial").data(0, Qt.UserRole + 2) == "已删除"

    # 单击只选中：不浏览、不接管现场会话，也不启用输入。
    window.navigation.list.setCurrentItem(_item(window, "history"))
    settle(window, tasks=False)
    assert window.page.reviewing is None
    assert window.controller.session_id == "initial"
    assert not window.input.isEnabled() and not window.send.isEnabled()

    # 双击才只读浏览：同样不接管现场会话，也不启用输入。
    _browse(window, "history")
    assert window.page.reviewing == "history"
    assert window.controller.session_id == "initial"
    assert not window.input.isEnabled() and not window.send.isEnabled()

    # 右键“继续”才重新激活会话。
    window.navigation._continue_session("history")
    settle(window)
    assert window.page.reviewing is None
    assert window.controller.session_id == "history"
    assert window.input.isEnabled() and window.send.isEnabled()
    assert not window.controller.loop.provider.requests


def test_delete_selected_history_returns_to_available_initial(window, monkeypatch):
    monkeypatch.setattr("sico_ui.navigation_actions.confirm",
                        lambda *args, **kwargs: True)
    activate(window, "history")
    deleted = window.controller
    window.navigation._delete("history")
    settle(window)
    assert deleted.closing
    assert window.controller.session_id == "initial"
    assert window.input.isEnabled() and window.send.isEnabled()
    assert _counts(window) == (1, 0)
    assert _item(window, "initial").data(0, Qt.UserRole + 2) is None


@pytest.mark.parametrize("selected", ["initial", "history"])
@pytest.mark.parametrize("damage", [
    "removed_log", "removed_directory", "recreated_codex_home",
    "directory_mode", "file_mode", "corrupt_log",
])
def test_missing_or_unreadable_initial_does_not_disable_other_sessions(window, selected, damage):
    initial = window.controller
    if selected == "history":
        activate(window, "history")
    directory = initial.journal.directory
    path = directory / "events.jsonl"
    if damage == "removed_log":
        path.unlink()
    elif damage in {"removed_directory", "recreated_codex_home"}:
        shutil.rmtree(directory)
        if damage == "recreated_codex_home":
            (directory / "codex").mkdir(parents=True)
            directory.chmod(0o775)
    elif damage == "directory_mode":
        directory.chmod(0o775)
    elif damage == "file_mode":
        path.chmod(0o644)
    else:
        path.write_bytes(b"not-json\n")
    before = path.read_bytes() if path.exists() else None
    _tick(window)
    assert _item(window, "initial").data(0, Qt.UserRole + 2) == "记录不可用"
    assert window.controller.session_id == selected
    assert window.input.isEnabled() == (selected == "history")
    assert window.send.isEnabled() == (selected == "history")
    for tree in _trees(window):
        assert not tree.signalsBlocked()
    window.navigation.return_live()
    assert any("初始会话已删除或不可用" in text for text in _alert_texts(window))
    assert window.controller.session_id == selected
    assert window.input.isEnabled() == (selected == "history")
    assert (path.read_bytes() if path.exists() else None) == before
    if damage in {"directory_mode", "recreated_codex_home"}:
        assert directory.stat().st_mode & 0o777 == 0o775


def test_disk_deletion_marker_is_respected_without_controller_closing(window):
    initial = window.controller
    initial.loop._event("codex.thread.deleted", {})
    assert not initial.closing
    _tick(window)
    assert _item(window, "initial").data(0, Qt.UserRole + 2) == "已删除"
    assert not window.input.isEnabled() and not window.send.isEnabled()
    window.navigation.return_live()
    assert any("初始会话已删除或不可用" in text for text in _alert_texts(window))
    assert not window.input.isEnabled() and not window.send.isEnabled()


def test_initial_missing_from_bounded_catalog_remains_usable(window, monkeypatch):
    activate(window, "history")
    snapshot = thaw(window.sessions.catalog.snapshot())
    snapshot["rows"] = tuple(row for row in snapshot["rows"] if row["id"] != "initial")
    monkeypatch.setattr(window.sessions.catalog, "snapshot", lambda *args: snapshot)
    _tick(window)
    assert _item(window, "initial").data(0, Qt.UserRole + 2) is None
    window.navigation.return_live()
    settle(window, tasks=False)
    assert window.controller.session_id == "initial"
    assert window.input.isEnabled() and window.send.isEnabled()


@pytest.mark.parametrize("modified", [None, "invalid", float("nan"), float("inf")])
def test_missing_or_invalid_modified_is_renderable(window, monkeypatch, modified):
    snapshot = thaw(window.sessions.catalog.snapshot())
    rows = snapshot["rows"]
    history = next(row for row in rows if row["id"] == "history")
    if modified is None:
        history.pop("modified")
    else:
        history["modified"] = modified
    monkeypatch.setattr(window.sessions.catalog, "snapshot", lambda *args: snapshot)
    _tick(window)
    assert "时间未知" in _item(window, "history").text(0)
    assert window.navigation.catalog_state is not None


@pytest.mark.parametrize("stage", ["catalog", "render"])
def test_qt_timer_errors_are_contained_and_next_refresh_recovers(window, monkeypatch, stage):
    def fail(*args):
        raise KeyError("modified")

    with monkeypatch.context() as broken:
        if stage == "catalog":
            broken.setattr(window.sessions.catalog, "snapshot", fail)
        else:
            broken.setattr(navigation_module, "QTreeWidgetItem", fail)
        _tick(window)
        assert "会话历史暂不可用" not in window.status.text()
        assert window.navigation.notices.messages["catalog"][0] == "会话历史暂不可用"
        assert window.navigation.catalog_state is None
        for tree in _trees(window):
            assert not tree.signalsBlocked()
    _tick(window)
    assert window.navigation.catalog_state is not None
    assert _counts(window) == (1, 1)


def test_stale_browse_rejects_deleted_record_without_reopening_it(window):
    from agent_gui_wait import wait_until

    item = _item(window, "initial")
    window.controller.delete()
    # Runtime closure alone cannot reject a readable archive. The replay worker
    # now checks the tombstone asynchronously, even if this row is still stale.
    failures = []
    window.page.failed.connect(failures.append)
    window.navigation.list.itemDoubleClicked.emit(item, 0)
    wait_until(lambda: failures and not window.page.opening)
    assert any("历史会话暂不可读" in text for text in failures)
    assert window.page.reviewing is None
    assert not window.input.isEnabled() and not window.send.isEnabled()
    assert window.controller.closing


def test_return_live_keeps_selected_history_enabled_after_initial_deleted(window):
    activate(window, "history")
    window.sessions.controllers["initial"].delete()
    window.navigation.return_live()
    assert window.controller.session_id == "history"
    assert window.input.isEnabled() and window.send.isEnabled()
    assert any("初始会话已删除或不可用" in text for text in _alert_texts(window))
    assert "初始会话已删除或不可用" not in window.status.text()


def test_delete_rejects_a_session_switch_during_confirmation(window, monkeypatch):
    def decision(*args, **kwargs):
        window.page.activate_session("history")
        return True

    monkeypatch.setattr("sico_ui.navigation_actions.confirm", decision)
    window.navigation._delete("initial")
    assert any("所选会话已切换" in text for text in _alert_texts(window))
    settle(window)
    assert all(not controller.closing for controller in window.sessions.controllers.values())
    assert window.controller.session_id == "history"
