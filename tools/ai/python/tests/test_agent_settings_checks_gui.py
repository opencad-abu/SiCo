"""AS3 editors and isolated check receipts keep project drafts and task ownership separate."""

from concurrent.futures import Future
from copy import deepcopy

import pytest
from agent_gui_wait import wait_until
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication
from sico_ui.agent_settings import AgentSettingsDialog
from sico_ui.mcp_resource_editor import McpResourceEditor
from test_agent_settings_gui import close, done, fake_window, response


def checked(root):
    return dict(status="checked", mcp_servers=[dict(id="demo", status="available",
                message="已连接", tools=["read"])], skills=[dict(name="same-name", enabled=True,
                path=str(root / "SKILL.md"), definition_path=str(root / "SKILL.md"),
                description="按完整路径选择的外部技能")], call_result=None)


@pytest.mark.parametrize("width", [620, 900])
def test_mcp_editor_both_transports_preserve_fractional_timeouts(tmp_path, width):
    editor = McpResourceEditor(None)
    try:
        editor.resize(width, 480)
        editor.show()
        for key, text in {"id": "my-service.v1", "name": "设计资料服务",
                          "command": "/usr/bin/python3",
                          "cwd": str(tmp_path)}.items():
            editor.fields[key].setText(text)
        editor.fields["args"].setPlainText("-m\nmy_mcp")
        editor.fields["env_refs"].setPlainText("TOKEN=DESIGN_MCP_TOKEN")
        editor.fields["startup_timeout_sec"].setValue(1.125)
        QApplication.instance().processEvents()
        assert editor.grab().save(str(tmp_path / "mcp-stdio.png"))
        editor.submit()
        assert editor.value["startup_timeout_sec"] == 1.125
        assert editor.value["env_refs"] == {"TOKEN": "DESIGN_MCP_TOKEN"}
        editor.transport.setCurrentIndex(1)
        editor.fields["url"].setText("https://example.invalid/mcp")
        editor.fields["credential_ref"].setText("DESIGN_MCP_TOKEN")
        editor.filter_tools.setChecked(True)
        editor.show()
        QApplication.instance().processEvents()
        assert editor.grab().save(str(tmp_path / "mcp-http.png"))
        editor.submit()
        assert editor.value["enabled_tools"] == []
        assert "command" not in editor.value
        assert editor.value["credential_ref"] == "DESIGN_MCP_TOKEN"
    finally:
        editor.close()


def test_check_is_single_owned_dialog_and_skill_choice_is_draft_only(tmp_path):
    window = fake_window(tmp_path, lambda _token: done(response(tmp_path)))
    requests = []
    def check(*args):
        requests.append(args)
        return done(checked(tmp_path))
    window.api.agent_settings.check = check
    window.api.agent_settings.cancel_check = lambda _id: done({})
    dialog = AgentSettingsDialog(window)
    try:
        wait_until(lambda: not dialog.busy)
        dialog.show()
        dialog.check_resources()
        probe = dialog.check_dialog
        dialog.check_resources()
        assert dialog.check_dialog is probe
        wait_until(lambda: not probe.active)
        assert len(requests) == 1 and requests[0][3] is None
        probe.rows.setCurrentItem(probe.rows.topLevelItem(1))
        assert probe.rows.currentItem().data(0, Qt.UserRole)["name"] == "same-name"
        assert probe.grab().save(str(tmp_path / "resource-check.png"))
        probe.add_override()
        assert dialog.check_dialog is None and dialog.draft.dirty
        assert dialog.draft.manifest["skill_overrides"] == [
            {"path": str(tmp_path / "SKILL.md"), "enabled": False}]
        assert not (tmp_path / ".sico").exists()
    finally:
        close(window, dialog)


def test_late_check_does_not_change_revised_draft_and_cancels_once(tmp_path):
    pending = Future()
    pending.set_running_or_notify_cancel()
    window = fake_window(tmp_path, lambda _token: done(response(tmp_path)))
    cancellations = []
    window.api.agent_settings.check = lambda *_args: pending
    window.api.agent_settings.cancel_check = lambda identity: cancellations.append(identity)
    dialog = AgentSettingsDialog(window)
    try:
        wait_until(lambda: not dialog.busy)
        dialog.check_resources()
        probe = dialog.check_dialog
        dialog.draft.replace("skill_overrides", -1,
                             {"path": str(tmp_path / "new/SKILL.md"), "enabled": True})
        before = deepcopy(dialog.draft.manifest)
        probe.check_scope()
        probe.check_scope()
        assert cancellations == [probe.identity]
        pending.set_result(checked(tmp_path))
        probe.receipt.poll()
        assert not probe.result and not probe.call.isEnabled()
        assert dialog.draft.manifest == before
    finally:
        close(window, dialog)


def test_explicit_call_requires_user_choice_and_keeps_raw_arguments_service_owned(tmp_path):
    window = fake_window(tmp_path, lambda _token: done(response(tmp_path)))
    requests = []
    window.api.agent_settings.check = lambda *args: requests.append(args) or done(checked(tmp_path))
    window.api.agent_settings.cancel_check = lambda _id: done({})
    dialog = AgentSettingsDialog(window)
    try:
        wait_until(lambda: not dialog.busy)
        dialog.check_resources()
        probe = dialog.check_dialog
        wait_until(lambda: not probe.active)
        probe.tool.setText("read")
        probe.arguments.setPlainText('{"query":"PDK 文档"}')
        probe.confirm_call()
        assert len(requests) == 1
        probe.confirmation.pick("cancel")
        assert len(requests) == 1
        probe.confirm_call()
        probe.confirmation.pick("call")
        wait_until(lambda: not probe.active)
        assert len(requests) == 2 and requests[-1][3]["arguments"] == '{"query":"PDK 文档"}'
    finally:
        close(window, dialog)
