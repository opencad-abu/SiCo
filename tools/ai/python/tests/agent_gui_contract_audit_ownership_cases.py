# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""Audit selection and answer ownership contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QRadioButton

from sico_ui.records import AuditPanel


def test_audit_request_selects_the_named_review_not_the_first_pending():
    """Two pending reviews with the same title must answer their own audit id."""
    app = QApplication.instance() or QApplication([])
    panel = AuditPanel("暂无交互记录")
    question = {"id": "ask_test", "header": "Ask 测试",
                "question": "这次只是测试吗？",
                "options": [{"label": "仅测试", "description": "不修改设计"}]}

    def audit(audit_id, view):
        return {
            "id": audit_id, "title": "Ask 测试", "status": "pending",
            "questions": [question],
            "context": {
                "target_id": "q_" + audit_id,
                "snapshot": {"cellview": {"lib": "SicoTest", "cell": "tb",
                                           "view_name": view}},
            },
        }

    audits = {"a_schematic": audit("a_schematic", "schematic"),
              "a_layout": audit("a_layout", "layout")}
    records = [
        {"audit_id": "a_schematic", "record": {"sequence": 1, "task_id": "t1"}},
        {"audit_id": "a_layout", "record": {"sequence": 2, "task_id": "t2"}},
    ]
    replies = []
    panel.answerRequested.connect(lambda audit_id, answers: replies.append((audit_id, answers)))
    try:
        panel.sync_records(records, audits)
        panel.select_audit("a_schematic")
        assert panel.editing_audit == "a_schematic"
        assert panel.select_audit("a_layout")
        assert panel.editing_audit == "a_layout"
        current = panel.list.currentItem()
        assert current.data(0, Qt.UserRole)["audit_id"] == "a_layout"
        assert "layout" in current.text(1)
        panel.choices.findChild(QRadioButton).setChecked(True)
        panel.submit_answer()
        assert [audit_id for audit_id, _ in replies] == ["a_layout"]
    finally:
        panel.close()


def test_audit_terminal_labels_distinguish_unanswered_from_undelivered():
    from sico_ui.records import AuditPanel, audit_title

    base = {
        "id": "a1", "title": "Ask 测试", "status": "invalid",
        "questions": [{"id": "q", "header": "问题", "question": "继续吗？",
                       "options": []}],
        "context": {"target_id": "q1", "snapshot": {
            "cellview": {"lib": "SicoTest", "cell": "tb",
                         "view_name": "schematic"}}},
        "reason": "来源窗口已关闭",
    }
    unanswered = AuditPanel.markdown(base)
    assert "问题已失效，尚未答复" in unanswered
    assert "尚无" not in unanswered
    assert "schematic" in audit_title(base)

    submitted = dict(base, reply={"answers": {"q": {"choice": "继续", "text": ""}}})
    delivered = AuditPanel.markdown(submitted)
    assert "答复未交付，问题已失效" in delivered
    assert "尚未答复" not in delivered

    unknown = dict(base, status="unconfirmed", reply={"answers": {}})
    assert "答复交付未确认" in AuditPanel.markdown(unknown)
