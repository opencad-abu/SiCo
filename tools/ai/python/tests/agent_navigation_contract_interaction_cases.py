# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent navigation contract interaction contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_gui_wait import activate, settle, wait_until
from agent_navigation_contract_support import (
    _alert_texts,
    _browse,
    _counts,
    _dot_color,
    _item,
    _tick,
    _tree_of,
    _trees,
)
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QLabel, QMenu, QTreeWidgetItem

import sico_ui.navigation as navigation_module
import sico_ui.navigation_menu as navigation_menu


def test_completed_child_tasks_are_silent_not_free():
    """Only live child tasks get activity; completed records are not blue."""
    from sico_ui.navigation_status import CHILD_ACTIVITY

    assert CHILD_ACTIVITY["started"] == "active"
    assert CHILD_ACTIVITY["completed"] == "idle"
    assert CHILD_ACTIVITY["failed"] == "pending"
    assert CHILD_ACTIVITY.get("unknown", "idle") == "idle"


def test_status_dot_is_rendered_in_front_of_every_name_without_clipping(window, app):
    window.show()
    settle(window, tasks=False)
    app.processEvents()
    window.navigation.refresh()
    app.processEvents()
    # 已打开的空闲会话在"当前"，是蓝色"空闲"；未打开的历史记录在"历史"，才是灰色"静默"。
    expected = {
        "initial": navigation_module.ACTIVITY["free"][1].getRgb()[:3],
        "history": navigation_module.ACTIVITY["idle"][1].getRgb()[:3],
    }
    assert _item(window, "initial").toolTip(0).startswith("空闲\ninitial")
    assert "双击浏览" in _item(window, "initial").toolTip(0)
    assert _item(window, "history").toolTip(0).startswith("静默\nhistory")
    assert _tree_of(window, "initial") is window.navigation.current
    assert _tree_of(window, "history") is window.navigation.list
    for tree in _trees(window):
        image = tree.viewport().grab().toImage()
        assert tree.topLevelItemCount() == 1
        for index in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(index)
            dot = expected[item.data(0, Qt.UserRole)]
            rect = tree.visualItemRect(item)
            columns = []
            for x in range(max(0, rect.left()), min(image.width(), rect.right() + 1)):
                for y in range(max(0, rect.top()), min(image.height(), rect.bottom() + 1)):
                    color = image.pixelColor(x, y)
                    if (color.red(), color.green(), color.blue()) == dot:
                        columns.append(x)
                        break
            assert columns, "status dot missing for " + item.data(0, Qt.UserRole)
            assert max(columns) - min(columns) + 1 >= 6, "status dot clipped"


def test_activating_a_silent_session_moves_it_to_the_current_pane(window, app):
    """从"历史"激活（继续）后，会话行移到上方"当前"并显示蓝色空闲。"""

    assert _tree_of(window, "history") is window.navigation.list
    activate(window, "history")
    _tick(window)
    assert _tree_of(window, "history") is window.navigation.current
    assert _counts(window) == (2, 0)
    free_dot = navigation_module.ACTIVITY["free"][1].getRgb()[:3]
    assert _dot_color(_item(window, "history")) == free_dot


def test_session_rows_stay_close_to_normal_line_spacing(window, app):
    """会话行按实际文本行数取正常行距，不再强制预留两行和额外空白。"""

    window.show()
    settle(window, tasks=False)
    app.processEvents()
    window.navigation.refresh()
    app.processEvents()
    line_spacing = window.navigation.list.fontMetrics().lineSpacing()
    item = _item(window, "history")
    lines = item.text(0).count("\n") + 1
    row_height = item.sizeHint(0).height()
    assert row_height == line_spacing * lines + 4


def test_single_click_selects_without_browsing(window, app):
    """单击只选中行：不进入只读浏览，也不接管现场会话。"""

    live = window.controller
    item = _item(window, "history")
    window.navigation.list.setCurrentItem(item)
    settle(window, tasks=False)
    # 刷新会重建行；选中状态按会话 id 保留，而不是旧的对象。
    selected = window.navigation.list.currentItem()
    assert selected is not None and selected.data(0, Qt.UserRole) == "history"
    assert window.page.reviewing is None
    assert _tree_of(window, "history") is window.navigation.list
    assert "浏览中" not in selected.text(0)
    assert window.controller is live
    assert window.input.isEnabled() and window.input.isReadOnly()
    wait_until(lambda: not window.send.isEnabled())


def test_double_click_browses_records_without_activating(window, app):
    """双击才只读回放：不接管会话，行仍留在"历史"，只是标上浏览中。"""

    live = window.controller
    assert "history" not in window.sessions.controllers
    _browse(window, "history")
    _tick(window)
    assert window.page.reviewing == "history"
    assert _tree_of(window, "history") is window.navigation.list
    assert "浏览中" in _item(window, "history").text(0)
    assert set(window.sessions.controllers) == {"initial"}
    assert window.controller is live
    assert not window.input.isEnabled() and not window.send.isEnabled()


def test_mouse_double_click_browses_an_expandable_row_without_collapsing(window, app):
    """真实双击即使行带子任务也只浏览：展开/折叠交给分支箭头或键盘。"""

    item = _item(window, "history")
    item.addChild(QTreeWidgetItem(["子任务\nchild-thread"]))
    item.setExpanded(True)
    expanded_at_click = []
    window.navigation.list.itemDoubleClicked.connect(
        lambda row, _column: expanded_at_click.append(row.isExpanded())
    )
    assert not window.navigation.list.expandsOnDoubleClick()
    window.show()
    settle(window, tasks=False)
    app.processEvents()
    point = window.navigation.list.visualItemRect(item).center()

    QTest.mouseClick(window.navigation.list.viewport(), Qt.LeftButton, Qt.NoModifier, point)
    app.processEvents()
    assert window.page.reviewing is None  # 单击只选中。

    QTest.mouseDClick(window.navigation.list.viewport(), Qt.LeftButton, Qt.NoModifier, point,
                      delay=30)
    settle(window, tasks=False)
    assert window.page.reviewing == "history"
    assert expanded_at_click == [True]


def test_session_panes_split_the_height_one_to_two(window, app):
    """上"当前"与下"历史"两块区域的默认高度约为 1:2。"""

    # 窄窗口会把左侧导航自动收成条状；这里量的是展开状态下两块区域的比例。
    window.resize(1280, 820)
    app.processEvents()
    window.show()
    settle(window, tasks=False)
    app.processEvents()
    window.navigation.refresh()
    app.processEvents()
    current = window.navigation.current.height()
    history = window.navigation.list.height()
    assert current > 0 and history > 0
    assert abs(current / history - 0.5) < 0.15, (current, history)


def test_session_panes_are_draggable_by_the_splitter(window, app):
    """两块区域之间有分割条：用户可以拖动调整，且两块都不会塌陷。"""

    window.show()
    settle(window, tasks=False)
    app.processEvents()
    window.navigation.refresh()
    app.processEvents()
    splitter = window.navigation.splitter
    assert splitter.count() == 2 and not splitter.childrenCollapsible()
    default = splitter.sizes()
    assert abs(default[0] / default[1] - 0.5) < 0.05, default
    splitter.moveSplitter(splitter.height() // 4, 1)
    app.processEvents()
    dragged = splitter.sizes()
    assert dragged[0] < default[0], (default, dragged)
    assert all(size > 0 for size in dragged)
    # 拖动后再缩放窗口：保持用户的选择，两块仍然都可见。
    window.resize(1100, 760)
    app.processEvents()
    assert all(size > 0 for size in splitter.sizes())


def test_session_blocks_show_no_region_titles(window, app):
    """两块区域没有“当前/历史”标题行：只有空块显示自己的占位文案。"""

    pane = window.navigation.session_pane
    labels = pane.findChildren(QLabel)
    assert {label.text() for label in labels} == {"暂无已打开的会话", "暂无历史会话"}
    assert [tree.objectName() for tree in _trees(window)] == ["sessionCurrent", "sessionHistory"]
    # 两块都有行（上＝已打开会话、下＝历史记录）：一个标题或占位都不显示。
    assert _counts(window) == (1, 1)
    assert not any(label.isVisibleTo(pane) for label in labels)

    # 历史记录激活后下块清空：只有下块显示占位，上块仍不出现任何标题。
    activate(window, "history")
    _tick(window)

    assert _counts(window) == (2, 0)
    assert not window.navigation.current_hint.isVisibleTo(pane)
    assert window.navigation.history_hint.isVisibleTo(pane)
    assert [label.text() for label in labels if label.isVisibleTo(pane)] == ["暂无历史会话"]


def test_double_click_previews_records_without_activating(window):
    live = window.controller
    assert "history" not in window.sessions.controllers

    _browse(window, "history")

    assert window.page.reviewing == "history"
    assert set(window.sessions.controllers) == {"initial"}
    assert window.controller is live
    assert not window.input.isEnabled() and not window.send.isEnabled()
    assert window.target.text().startswith("历史会话（只读）")
    assert "浏览" in window.status.text()
    assert window.return_action.isEnabled()
    assert not live.loop.provider.requests
    assert "浏览中" in _item(window, "history").text(0)
    assert "当前会话" in _item(window, "initial").text(0)


def test_context_menu_offers_continue_rename_delete_for_the_selection(window, monkeypatch):
    actions = []

    class Menu(QMenu):
        chosen = None

        def addAction(self, text):
            action = super().addAction(text)
            actions.append((text, action))
            return action

        def exec_(self, *args):
            return actions[Menu.chosen][1] if Menu.chosen is not None else None

    monkeypatch.setattr(navigation_menu, "QMenu", Menu)
    monkeypatch.setattr(window.navigation.list, "itemAt", lambda position: _item(window, "history"))
    window.navigation._menu(window.navigation.list.viewport().rect().center(),
                            window.navigation.list)
    assert [text for text, _action in actions] == ["继续", "重命名", "删除", "新建标签…"]
    assert actions[0][1].isEnabled()

    actions.clear()
    live_item = _item(window, "initial")
    monkeypatch.setattr(window.navigation.list, "itemAt", lambda position: live_item)
    window.navigation._menu(window.navigation.list.viewport().rect().center(),
                            window.navigation.list)
    assert actions[0][1].isEnabled()  # 当前菜单允许显式继续现场会话。
    assert window.controller.session_id == "initial"

    # “继续”是唯一重新激活会话的入口，且不执行已排队的请求。
    Menu.chosen = 0
    actions.clear()
    monkeypatch.setattr(window.navigation.list, "itemAt", lambda position: _item(window, "history"))
    window.navigation._menu(window.navigation.list.viewport().rect().center(),
                            window.navigation.list)
    settle(window)
    assert window.page.reviewing is None
    assert window.controller.session_id == "history"
    assert window.input.isEnabled() and window.send.isEnabled()
    assert not window.sessions.controllers["history"].loop.provider.requests


def test_delete_previewed_session_keeps_live_session_and_view(window, monkeypatch):
    live = window.controller
    monkeypatch.setattr("sico_ui.navigation_actions.confirm",
                        lambda *args, **kwargs: True)
    _browse(window, "history")
    assert window.page.reviewing == "history"

    window.navigation._delete("history")
    settle(window)

    assert window.page.reviewing is None
    assert window.controller is live
    assert set(window.sessions.controllers) == {"initial"}
    assert window.input.isEnabled() and window.send.isEnabled()
    assert _counts(window) == (1, 0)
    assert not live.loop.provider.requests
    assert "只读" not in window.target.text()


def test_rename_selected_closed_session_does_not_activate_it(window, monkeypatch):
    live = window.controller
    monkeypatch.setattr("sico_ui.navigation_actions.ask_text",
                        lambda *args, **kwargs: ("历史复盘", True))
    window.navigation.list.setCurrentItem(_item(window, "history"))
    settle(window, tasks=False)

    window.navigation._rename("history")
    settle(window)

    assert not _alert_texts(window)
    assert set(window.sessions.controllers) == {"initial"}
    assert window.controller is live
    assert window.page.reviewing is None
    assert "历史复盘" in _item(window, "history").text(0)
    # 重命名重建列表后，单击选中的行仍然保持选中。
    assert window.navigation.list.currentItem().data(0, Qt.UserRole) == "history"
    assert not live.loop.provider.requests
