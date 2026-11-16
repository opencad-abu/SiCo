"""Editing and late receipts remain scoped through closing and navigation."""

from concurrent.futures import Future
from copy import deepcopy
from types import SimpleNamespace

import pytest
from agent_gui_wait import wait_until
from agent_worker_fixtures import heartbeat
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication
from sico_ui.agent_settings import AgentSettingsDialog
from test_agent_settings_gui import add_skill, close, done, fake_window, response


def test_escape_cancel_preserves_draft_then_cleans_receipts(tmp_path):
    window = fake_window(tmp_path, lambda _token: done(response(tmp_path)))
    dialog = AgentSettingsDialog(window)
    try:
        dialog.show()
        wait_until(lambda: not dialog.busy)
        add_skill(dialog, tmp_path / "draft")
        QTest.keyClick(dialog, Qt.Key_Escape)
        assert dialog.confirmation is not None and dialog.isVisible()
        dialog.confirmation.pick("cancel")
        assert dialog.draft.dirty and dialog.isVisible()
        QTest.keyClick(dialog, Qt.Key_Escape)
        dialog.confirmation.pick("continue")
        assert dialog.closed and not dialog.isVisible() and not dialog.timer.isActive()
        assert dialog.receipt.pending is None
    finally:
        close(window, dialog)


@pytest.mark.parametrize("change", ["home", "target", "connection"])
def test_scope_change_drops_delayed_read(tmp_path, change):
    pending = Future()
    pending.set_running_or_notify_cancel()
    window = fake_window(tmp_path, lambda _token: pending)
    window.show()
    if change == "target":
        window.page = SimpleNamespace(activation=1, reviewing="history", opening=False)
        window.presentation = SimpleNamespace(displayed_context="first")
    dialog = AgentSettingsDialog(window)
    try:
        dialog.show()
        heartbeat(window)
        if change == "home":
            window.hide()
        elif change == "target":
            window.presentation.displayed_context = "second"
        else:
            window.api = None
        pending.set_result(response(tmp_path))
        dialog.receipt.poll()
        dialog.check_scope()
        assert dialog.stale and dialog.draft.manifest is None
        assert not dialog.ui.reload.isEnabled()
    finally:
        close(window, dialog)


def test_pending_save_close_does_not_claim_to_cancel_accepted_write(tmp_path):
    pending = Future()
    pending.set_running_or_notify_cancel()
    window = fake_window(tmp_path, lambda _token: done(response(tmp_path)), lambda *_a: pending)
    dialog = AgentSettingsDialog(window)
    try:
        dialog.show()
        wait_until(lambda: not dialog.busy)
        add_skill(dialog, tmp_path / "draft")
        draft = deepcopy(dialog.draft.manifest)
        dialog.save()
        heartbeat(window)
        dialog.reject()
        assert "可能已提交" in dialog.confirmation.message.text()
        dialog.confirmation.pick("continue")
        pending.set_result({"outcome": "saved", "document": response(tmp_path)["project_defaults"]})
        QApplication.instance().processEvents()
        assert dialog.closed and dialog.draft.manifest == draft
        assert not pending.cancelled() and dialog.receipt.pending is None
    finally:
        close(window, dialog)


def test_plugin_edit_duplicate_toggle_and_remove_only_change_draft(tmp_path):
    window = fake_window(tmp_path, lambda _token: done(response(tmp_path)))
    dialog = AgentSettingsDialog(window)
    try:
        dialog.show()
        wait_until(lambda: not dialog.busy)
        dialog.edit("plugins")
        editor = dialog.editor
        editor.identity.setText("plugin")
        editor.name.setText("design")
        editor.path.setText(str(tmp_path / "marketplace.json"))
        assert editor.grab().save(str(tmp_path / "plugin-editor.png"))
        editor.submit()
        dialog.edit("plugins", 0)
        dialog.editor.name.setText("updated")
        dialog.editor.submit()
        assert dialog.draft.manifest["plugins"][0]["name"] == "updated"
        dialog.toggle("plugins", 0)
        assert not dialog.draft.manifest["plugins"][0]["enabled"]
        dialog.edit("skill_roots")
        dialog.editor.identity.setText("plugin")
        dialog.editor.path.setText(str(tmp_path))
        dialog.editor.browse()
        assert dialog.editor.picker.isVisible()
        dialog.editor.picker.pick("cancel")
        dialog.editor.submit()
        assert "重复" in dialog.ui.message.text()
        assert dialog.draft.manifest["skill_roots"] == []
        dialog.remove("plugins", 0)
        assert not dialog.draft.dirty
    finally:
        close(window, dialog)
