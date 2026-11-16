"""Native approval details stay visible beside explicit one-shot choices."""

from copy import deepcopy

from agent_gui_wait import wait_until
from agent_window_fixture import create_window
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QRadioButton
from test_codex_access import request
from test_codex_elicitation import running  # noqa: F401
from test_codex_elicitation_gui import app  # noqa: F401

from sico_ui.records import AuditPanel
from sico_ui.theme import ACCENT


def test_approval_displays_full_operation_without_default_consent(running, app, tmp_path):
    loop, _, _ = running
    loop._input_request(request(command="printf '<tag> ```' > /outside/review"), "turn")
    row = next(reversed(loop.audit.index.audits.values()))
    panel = AuditPanel("暂无交互")
    panel.resize(480, 720)
    panel.show()
    try:
        item = panel.add_audit(deepcopy(row))
        panel.select(item)
        app.processEvents()
        assert panel.detail.isVisible()
        assert "/outside/review" in panel.detail.toPlainText()
        assert "<tag> ```" in panel.detail.toPlainText()
        # 命令以等宽代码块完整呈现，不再挤在 JSON 转义里。
        markdown = panel.markdown(row)
        assert "**命令**（/project）：" in markdown
        assert "monospace" in panel.detail.document().toHtml()
        choices = panel.choices.findChildren(QRadioButton)
        assert len(choices) == 3 and not any(c.isChecked() for c in choices)
        assert all(not edit.isVisible() for edit in panel.text_edits.values())
        assert panel.grab().save(str(tmp_path / "native-approval.png"))
    finally:
        panel.close()


def test_pending_audit_record_is_painted_accent(running, app):
    """待答复的审阅记录用红棕色标出；答复后恢复默认色。"""
    loop, _controller, _sent = running
    loop._input_request(request(), "turn")
    row = next(reversed(loop.audit.index.audits.values()))
    assert row["status"] == "pending"
    panel = AuditPanel("暂无交互")
    try:
        item = panel.add_audit(deepcopy(row))
        app.processEvents()
        assert item.foreground(1).color().name() == QColor(ACCENT).name()
        panel.audit_rows[row["id"]] = dict(row, status="answered")
        panel.render_audit(row["id"])
        assert item.foreground(1).style() == Qt.NoBrush
    finally:
        panel.close()


def test_approval_answer_refreshes_chat_status_before_the_next_model_event(running, app):
    """答复后聊天状态立即从等待答复切到后续核验，不等模型再发一帧。"""
    loop, controller, _sent = running
    loop.validate_audit_target = lambda: None
    window = create_window(controller)
    window.resize(900, 650)
    window.show()
    try:
        assert loop._input_request(request("commandExecution", environmentId="local"), "turn")
        wait_until(lambda: (window.presentation.state or {}).get("task", {}).get(
            "activity", {}).get("phase") == "waiting_user")
        assert window.status.text().startswith("等待答复")
        row = next(reversed(loop.audit.index.audits.values()))

        assert controller.backend_commands.answer_audit(
            controller.session_id, row["task_id"], row["id"],
            {"codex_approval": {"choice": "仅批准本次", "text": ""}}, "reply-gui",
        )
        assert loop.audit.deliver() == 1
        wait_until(lambda: window.presentation.state["task"].get("activity", {}).get(
            "phase") == "waiting_model")
        assert "waiting_audits" not in window.presentation.state["task"]
        assert window.status.text().startswith("答复已发送，等待服务后续结果")
    finally:
        window.chat_scroll.reset()
        window.sessions.close()
        assert window.sessions.wait(5)
        window.close()
