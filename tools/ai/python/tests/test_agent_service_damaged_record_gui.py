"""Incomplete rows remain selectable for deletion while browsing and continuation are blocked."""

import pytest
from agent_gui_wait import wait_until
from agent_service_gui_fixtures import remote_window
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMenu
from sico.storage.record_removal import removed
from sico_ui.service_connection import attach_service_connection
from test_agent_service_continue_ended import ended
from test_agent_service_damaged_record import damage


@pytest.mark.parametrize("choice", ["delete", "cancel", "changed"])
def test_damaged_history_menu_only_offers_delete(tmp_path, monkeypatch, choice):
    ended(tmp_path, monkeypatch)
    with remote_window(tmp_path, monkeypatch) as (service, window):
        attach_service_connection(window)
        window.show()
        damage(tmp_path, "snapshot")
        service.dispatch.frontend.workspace.catalog.request().result(5)
        def row():
            window.navigation.refresh()
            return next((item for item in window.navigation._session_items()
                         if item.data(0, Qt.UserRole) == "old"
                         and item.data(0, Qt.UserRole + 3)), None)
        wait_until(lambda: row() is not None)
        item = row()
        assert item.flags() & Qt.ItemIsEnabled
        notices, shown = [], []
        monkeypatch.setattr(window.navigation, "_alert",
                            lambda message, *_: notices.append(message))
        monkeypatch.setattr(window.page, "preview_session",
                            lambda *_: pytest.fail("damaged row cannot browse"))
        window.navigation.select(item)
        assert window.navigation.selected_id == "old"
        assert not notices
        for _ in range(3):
            window.navigation.refresh()
        assert window.navigation.selected_id == "old"
        assert not notices
        item = row()
        window.navigation.browse(item)
        assert notices
        def confirm(_parent, title, message, **_options):
            shown.append(title)
            assert "不能浏览或恢复" in message
            if choice == "changed":
                path = tmp_path / ".sico/ai/agent/sessions/old/new-content"
                path.write_text("new content")
                path.chmod(0o600)
            return choice != "cancel"
        class Menu(QMenu):
            def exec_(self, *_args):
                assert [action.text() for action in self.actions()] == ["删除"]
                return self.actions()[0]
        monkeypatch.setattr("sico_ui.navigation_menu.QMenu", Menu)
        monkeypatch.setattr("sico_ui.navigation_actions.confirm", confirm)
        tree = item.treeWidget()
        window.navigation._menu(tree.visualItemRect(item).center(), tree)
        wait_until(lambda: bool(shown))
        assert shown == ["删除残留记录"]
        root = tmp_path / ".sico/ai/agent"
        if choice == "delete":
            wait_until(lambda: removed(root, "old"))
            wait_until(lambda: row() is None)
        elif choice == "changed":
            wait_until(lambda: any("记录已变化" in text for text in notices))
        assert removed(root, "old") == (choice == "delete")
