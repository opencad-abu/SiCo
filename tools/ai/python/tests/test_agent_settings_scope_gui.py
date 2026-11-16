"""Settings scope, late receipts and secondary editors keep drafts local."""

from __future__ import annotations

from types import SimpleNamespace

from agent_gui_wait import wait_until
from PyQt5.QtCore import QEvent, QObject
from PyQt5.QtWidgets import QDialog
from sico_ui.agent_settings import AgentSettingsDialog, show_agent_settings
from test_agent_settings_gui import close, done, fake_window, response


def opened(tmp_path):
    window = fake_window(tmp_path, lambda _token: done(response(tmp_path)))
    dialog = AgentSettingsDialog(window)
    wait_until(lambda: not dialog.busy)
    return window, dialog


def test_current_rejects_ownership_errors_and_scope_changes(tmp_path):
    window, dialog = opened(tmp_path)
    try:
        dialog.token = object()
        window.api.owns = lambda _token: (_ for _ in ()).throw(ValueError("gone"))
        assert dialog.current() is False

        dialog.token = None
        window.api.owns = lambda _token: True
        assert dialog.current() is True
        window.lifecycle.closing = True
        assert dialog.current() is False
        window.lifecycle.closing = False
        window.page = SimpleNamespace(activation=dialog.activation,
                                      reviewing=dialog.reviewing, opening=True)
        dialog.page = window.page
        window.page.opening = True
        assert dialog.current() is False
        window.page.opening = False
        dialog.closed = True
        assert dialog.current() is False
    finally:
        close(window, dialog)


def test_hide_invalidates_or_discards_owned_children(tmp_path, monkeypatch):
    window, dialog = opened(tmp_path)
    event = QEvent(QEvent.Hide)
    try:
        calls = []
        monkeypatch.setattr(dialog, "discard_close", lambda: calls.append("discard"))
        window.lifecycle.closing = True
        dialog.eventFilter(window, event)
        assert calls == ["discard"]

        window.lifecycle.closing = False
        dialog.page = object()
        dialog.eventFilter(window, event)
        dialog.page = None
        original = dialog.invalidate
        monkeypatch.setattr(dialog, "invalidate",
                            lambda: (calls.append("invalidate"), original()))
        dialog.eventFilter(window, event)
        assert calls[-1] == "invalidate"
        dialog.eventFilter(window, QEvent(QEvent.Show))
        dialog.eventFilter(QObject(), event)

        rejected = []
        dialog.editor = SimpleNamespace(reject=lambda: rejected.append("editor"))
        dialog.confirmation = SimpleNamespace(
            reject=lambda: rejected.append("confirmation"))
        original()
        assert dialog.stale is True
        assert rejected == ["editor", "confirmation"]
        assert "草稿保留" in dialog.ui.message.text()
    finally:
        close(window, dialog)


def test_session_label_handles_preview_live_and_missing_identity(tmp_path):
    window, dialog = opened(tmp_path)
    try:
        assert dialog.session_label() == ""
        dialog.token = "history"
        assert dialog.session_label() == "会话：历史记录 history · 只读"

        token = object()
        dialog.token = token
        assert dialog.session_label() == "会话：当前会话 · 只读"
        window.api.display_name = lambda value: "live-name" if value is token else "?"
        assert dialog.session_label() == "会话：live-name · 只读"
        window.api.display_name = lambda _value: (_ for _ in ()).throw(OSError("gone"))
        assert dialog.session_label() == "会话：当前会话 · 只读"
    finally:
        close(window, dialog)


def test_recheck_session_is_guarded_and_updates_the_snapshot(tmp_path, monkeypatch):
    window, dialog = opened(tmp_path)
    try:
        token = object()
        dialog.token = token
        window.api.owns = lambda _token: True
        dialog.response["session"] = {
            "status": "available", "recorded": False, "snapshot": {"busy": True}}
        commands = []
        window.api.command = lambda value, method: commands.append((value, method)) or "future"
        assert dialog.can_recheck() is True
        monkeypatch.setattr(
            dialog, "request",
            lambda future, success: success({"busy": False, "rows": []}))
        dialog.recheck_session()
        assert commands == [(token, "resource_status")]
        assert dialog.response["session"]["snapshot"] == {"busy": False, "rows": []}
        assert "已重新检查" in dialog.ui.message.text()

        dialog.busy = True
        dialog.recheck_session()
        dialog.busy = False
        failed = []
        monkeypatch.setattr(dialog, "failed", failed.append)
        window.api.command = lambda *_args: (_ for _ in ()).throw(RuntimeError("closed"))
        dialog.recheck_session()
        assert len(failed) == 1

        dialog.response["session"] = {"status": "unavailable"}
        dialog.recheck_session()
        assert len(commands) == 1
        dialog.session_rechecked("not-a-mapping")
    finally:
        close(window, dialog)


def test_late_request_result_checks_scope_before_success(tmp_path, monkeypatch):
    window, dialog = opened(tmp_path)
    try:
        captured = {}
        monkeypatch.setattr(dialog.receipt, "watch",
                            lambda future, success, failure:
                            captured.update(future=future, success=success,
                                            failure=failure))
        checked, succeeded = [], []
        monkeypatch.setattr(dialog, "check_scope", lambda: checked.append(True))
        dialog.closed = True
        dialog.request("future", succeeded.append)
        assert dialog.busy is True
        captured["success"]({"value": 1})
        assert dialog.busy is False and checked == [True] and succeeded == []

        dialog.closed = False
        dialog.request("future", succeeded.append)
        captured["success"]({"value": 2})
        assert succeeded == [{"value": 2}]
    finally:
        close(window, dialog)


def test_reload_and_loaded_report_unavailable_sections(tmp_path, monkeypatch):
    window, dialog = opened(tmp_path)
    try:
        dialog.busy = True
        dialog.reload()
        dialog.busy = False
        failed = []
        monkeypatch.setattr(dialog, "failed", failed.append)
        window.api.agent_settings.read = lambda _token: (_ for _ in ()).throw(OSError("gone"))
        dialog.reload()
        assert str(failed[0]) == "gone"

        value = response(tmp_path)
        value["model_settings"] = {"status": "unavailable", "message": "model unreadable"}
        value["data_sources"] = {"status": "unavailable", "project": None,
                                 "revision": None}
        value["project_defaults"] = {"status": "unavailable", "message": "default unreadable"}
        dialog.loaded(value)
        assert dialog.ui.model_note.text() == "model unreadable"
        assert dialog.data_saved == {} and dialog.data_revision is None
        assert "原有草稿保留" in dialog.ui.message.text()
    finally:
        close(window, dialog)


def test_failed_marks_inflight_writes_blocked_then_reports_current_error(
        tmp_path, monkeypatch):
    window, dialog = opened(tmp_path)
    try:
        dialog.saving = dialog.data_saving = True
        dialog.closed = True
        checked = []
        monkeypatch.setattr(dialog, "check_scope", lambda: checked.append(True))
        dialog.failed(RuntimeError("lost"))
        assert dialog.draft.blocked is True
        assert dialog.data_blocked is True
        assert dialog.saving is False and dialog.data_saving is False
        assert checked == [True]

        dialog.closed = False
        dialog.failed(RuntimeError("write failed"))
        assert "write failed" in dialog.ui.message.text()
        assert "草稿已保留" in dialog.ui.message.text()
    finally:
        close(window, dialog)


def test_save_guards_and_transport_failures_block_the_draft(tmp_path, monkeypatch):
    window, dialog = opened(tmp_path)
    try:
        dialog.save()
        dialog.draft.replace("skill_roots", -1, {
            "id": "draft", "path": str(tmp_path), "source": "project",
            "access": "read", "enabled": True})
        assert dialog.draft.dirty is True
        dialog.draft.blocked = True
        dialog.save()
        dialog.draft.blocked = False
        monkeypatch.setattr(dialog, "can_edit", lambda: True)
        calls = []
        window.api.agent_settings.save = lambda *_args: (
            calls.append(True) or (_ for _ in ()).throw(RuntimeError("offline")))
        dialog.save()
        assert calls == [True]
        assert dialog.draft.blocked is True
        assert "offline" in dialog.ui.message.text()
    finally:
        close(window, dialog)


def test_model_save_guards_and_conflict_reload(tmp_path, monkeypatch):
    window, dialog = opened(tmp_path)
    try:
        monkeypatch.setattr(dialog, "can_edit", lambda: False)
        dialog.save_model()
        monkeypatch.setattr(dialog, "can_edit", lambda: True)
        dialog.save_model()
        assert "未变化" in dialog.ui.message.text()

        dialog.ui.backend.setCurrentIndex(1)
        window.api.agent_settings.save_model = lambda *_args: (
            (_ for _ in ()).throw(OSError("model offline")))
        dialog.save_model()
        assert "model offline" in dialog.ui.message.text()

        reloaded = []
        monkeypatch.setattr("sico_ui.agent_settings.QTimer.singleShot",
                            lambda _delay, callback: reloaded.append(callback))
        dialog.ui.message.clear()
        dialog.model_result({"outcome": "uncertain", "message": "model uncertain"})
        assert dialog.ui.message.text() == "model uncertain"
        assert len(reloaded) == 1
        dialog.model_result({"outcome": "rejected", "message": "model rejected"})
        assert len(reloaded) == 1
    finally:
        close(window, dialog)


def test_data_source_clear_and_save_guards(tmp_path, monkeypatch):
    window, dialog = opened(tmp_path)
    try:
        monkeypatch.setattr(dialog, "can_edit_data", lambda: False)
        dialog.clear_data_source("pdk_data")
        assert dialog.data_edits == {}
        monkeypatch.setattr(dialog, "can_edit_data", lambda: True)
        dialog.clear_data_source("pdk_data")
        assert dialog.data_edits == {"pdk_data": None}
        assert "清除" in dialog.ui.message.text()

        dialog.data_edits = {}
        dialog.save_data_sources()
        dialog.data_edits = {"pdk_data": None}
        dialog.data_blocked = True
        dialog.save_data_sources()
        dialog.data_blocked = False
        failed = []
        monkeypatch.setattr(dialog, "failed", failed.append)
        window.api.agent_settings.save_data_sources = lambda *_args: (
            (_ for _ in ()).throw(ValueError("bad data")))
        dialog.save_data_sources()
        assert dialog.data_saving is False and str(failed[0]) == "bad data"

        dialog.data_result({"outcome": "conflict", "message": "data conflict"})
        assert dialog.data_blocked is True
        assert dialog.ui.message.text() == "data conflict"
    finally:
        close(window, dialog)


def test_data_source_editor_acceptance_rechecks_edit_scope(tmp_path, monkeypatch):
    window, dialog = opened(tmp_path)
    try:
        editors = []

        class Editor:
            def __init__(self, parent, kind, current):
                self.value = "/pdk"
                self.finished = SimpleNamespace(connect=lambda callback:
                                                setattr(self, "callback", callback))
                editors.append((self, parent, kind, current))

            def open(self):
                pass

            def deleteLater(self):
                pass

        monkeypatch.setattr("sico_ui.agent_settings.DataSourceEditor", Editor)
        monkeypatch.setattr(dialog, "can_edit_data", lambda: True)
        dialog.edit_data_source("pdk_data")
        editor = editors[-1][0]
        editor.callback(QDialog.Accepted)
        assert dialog.data_edits == {"pdk_data": "/pdk"}
        assert "加入草稿" in dialog.ui.message.text()

        monkeypatch.setattr(dialog, "can_edit_data", lambda: False)
        editor.callback(QDialog.Accepted)
        dialog.editor = object()
        dialog.edit_data_source("circuit_templates")
        assert len(editors) == 1
    finally:
        close(window, dialog)


def test_secondary_resource_editor_choice_and_knowledge_alias(tmp_path, monkeypatch):
    window, dialog = opened(tmp_path)
    try:
        created = []

        class Editor:
            def __init__(self, *args):
                self.value = {"id": "value"}
                self.finished = SimpleNamespace(connect=lambda callback:
                                                setattr(self, "callback", callback))
                created.append((self, args))

            def open(self):
                pass

            def deleteLater(self):
                pass

        monkeypatch.setattr("sico_ui.mcp_resource_editor.McpResourceEditor", Editor)
        monkeypatch.setattr("sico_ui.skill_override_editor.SkillOverrideEditor", Editor)
        monkeypatch.setattr("sico_ui.knowledge_editor.KnowledgeEditor", Editor)
        monkeypatch.setattr("sico_ui.agent_resource_editor.ResourceEditor", Editor)
        monkeypatch.setattr("sico_ui.agent_settings.ResourceEditor", Editor)
        monkeypatch.setattr(dialog, "can_edit", lambda: True)
        dialog.edit("knowledge_remote")
        assert created[-1][0].value == {"id": "value"}
        assert created[-1][1][2] == "remote"
        dialog.edit("skill_roots")
        assert len(created) == 1
        created[-1][0].callback(QDialog.Rejected)
        dialog.editor = None
        dialog.edit("skill_roots")
        assert len(created) == 2
        monkeypatch.setattr(dialog, "can_edit", lambda: False)
        dialog.toggle("skill_roots", 0)
        dialog.remove("skill_roots", 0)
    finally:
        close(window, dialog)


def test_show_agent_settings_reports_a_window_without_the_api():
    notices = []
    window = SimpleNamespace(api=None, info_app=SimpleNamespace(
        notice=lambda *args: notices.append(args)))
    show_agent_settings(window)
    assert notices == [("智能体设置暂不可用",
                        "请先连接支持设置管理的工程服务。")]
