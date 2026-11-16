"""Archive browsing and continuation through the existing window's Qt controls."""

import pytest
from agent_gui_wait import wait_until
from agent_service_gui_fixtures import remote_window
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QContextMenuEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QMenu

from sico_ui import navigation_menu
from sico_ui.service_connection import attach_service_connection


def session_row(window, key):
    return next((item for item in window.navigation._session_items()
                 if item.data(0, Qt.UserRole) == key), None)


def choose_menu(window, key, label, monkeypatch):
    selected = []

    class Menu(QMenu):
        def exec_(self, *args):
            action = next(action for action in self.actions() if action.text() == label)
            assert action.isEnabled()

            def click():
                selected.append(label)
                QTest.mouseClick(self, Qt.LeftButton, pos=self.actionGeometry(action).center())

            QTimer.singleShot(0, click)
            return super().exec_(*args)

    monkeypatch.setattr(navigation_menu, "QMenu", Menu)
    item = session_row(window, key)
    tree = item.treeWidget()
    tree.scrollToItem(item)
    position = tree.visualItemRect(item).center()
    event = QContextMenuEvent(QContextMenuEvent.Mouse, position,
                             tree.viewport().mapToGlobal(position))
    QApplication.sendEvent(tree.viewport(), event)
    assert selected == [label]


def browse_row(window, key):
    item = session_row(window, key)
    tree = item.treeWidget()
    tree.scrollToItem(item)
    position = tree.visualItemRect(item).center()
    QTest.mouseClick(tree.viewport(), Qt.LeftButton, pos=position)
    QTest.mouseDClick(tree.viewport(), Qt.LeftButton, pos=position)


@pytest.mark.parametrize("other_selected", [False, True])
@pytest.mark.parametrize("browse_first", [False, True])
def test_archived_conversation_is_usable_without_reopening_window(
        tmp_path, monkeypatch, other_selected, browse_first):
    with remote_window(tmp_path, monkeypatch) as (service, window):
        attach_service_connection(window)
        window.show()
        token = window.page.session
        window.input.setPlainText("message before archive")
        wait_until(lambda: window.send.isEnabled())
        window.send.click()
        wait_until(lambda: service.provider.seen == ["message before archive"]
                   and not service.owner.controllers[token.session_id].busy)
        if other_selected:
            window.new_session()
            wait_until(lambda: window.page.session != token and not window.page.opening)
        selected = window.page.session
        wait_until(lambda: session_row(window, token.session_id) is not None)
        choose_menu(window, token.session_id, "结束并归档", monkeypatch)
        window.service_connection.ending.dialog.button("accept").click()
        wait_until(lambda: token.session_id not in service.owner.controllers
                   and window.api.is_closing(token))
        def archived():
            window.navigation.refresh()
            item = session_row(window, token.session_id)
            return item is not None and item.treeWidget() is window.navigation.list

        wait_until(archived)
        item = session_row(window, token.session_id)
        assert item.treeWidget() is window.navigation.list
        assert item.flags() & Qt.ItemIsEnabled
        assert item.flags() & Qt.ItemIsSelectable
        assert not item.data(0, Qt.UserRole + 2)
        assert "已结束" in item.text(0)

        if browse_first:
            browse_row(window, token.session_id)
            wait_until(lambda: window.page.reviewing == token.session_id
                       and not window.page.opening)
            assert "message before archive" in window.display.toPlainText()
            assert not window.input.isEnabled() and not window.send.isEnabled()
            assert window.page.session == selected
            assert token.session_id not in service.owner.controllers
            # A catalog refresh must not eject an otherwise readable archive.
            service.dispatch.frontend.workspace.catalog.request().result(5)
            window.navigation.refresh()
            assert window.page.reviewing == token.session_id

        choose_menu(window, token.session_id, "继续", monkeypatch)
        wait_until(lambda: window.page.session.session_id == token.session_id
                   and window.page.session.runtime_id != token.runtime_id
                   and not window.page.opening and window.input.isEnabled())
        assert window.page.reviewing is None
        assert window.api.can_control(window.page.session)
        wait_until(lambda: "message before archive" in window.display.toPlainText())
        assert session_row(window, token.session_id).treeWidget() is window.navigation.current
        assert service.provider.seen == ["message before archive"]
        window.input.setPlainText("message after archive")
        wait_until(lambda: window.send.isEnabled())
        window.send.click()
        wait_until(lambda: service.provider.seen == ["message before archive", "message after archive"])
        assert not window.lifecycle.closing
