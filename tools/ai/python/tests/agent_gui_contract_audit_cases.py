# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""agent gui contract audit contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent_gui_wait import settle
from agent_window_fixture import create_window as AssistantWindow
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QApplication, QRadioButton, QTextBrowser

from sico.core.contracts import BoundContext
from sico.service.python_backend import PythonBackend
from sico.core.tools import ToolRegistry
from sico.providers.mock import ContextDemoProvider
from sico.storage.journal import SessionJournal
from sico_ui.theme import ACCENT
from sico_ui.transcript import Transcript


def test_feedback_text_shows_the_localized_recommendation_marker():
    transcript = Transcript(username="system-user")
    row = {
        "id": "audit-1", "title": "选择 PDK",
        "questions": [{
            "id": "pdk_library", "header": "选择 PDK",
            "question": "本次电路任务使用哪个 PDK？",
            "options": [
                {"label": "smic28", "description": "/pdk/smic28"},
                {"label": "smic28hkmg (Recommended)", "description": "/pdk/smic28hkmg"},
            ],
        }],
    }
    transcript.receive({
        "kind": "workbench.audit.prepared",
        "payload": row,
        "session_id": "session",
    })
    transcript.receive({
        "kind": "workbench.audit.opened",
        "payload": {"id": "audit-1", "source": "pdk_preparation"},
        "session_id": "session",
    })
    transcript.receive({
        "kind": "workbench.audit.reply_received",
        "payload": {"id": "audit-1", "reply_id": "reply-1",
                    "answers": {"pdk_library": {"choice": "smic28hkmg (Recommended)", "text": ""}}},
        "session_id": "session",
    })
    app = QApplication.instance() or QApplication([])
    view = QTextBrowser()
    transcript.render(view)
    shown = view.toPlainText()
    assert "选择 PDK：smic28hkmg（推荐值）" in shown
    assert "(Recommended)" not in shown


def test_audit_recommended_option_uses_the_accent_colour():
    from PyQt5.QtGui import QColor
    from PyQt5.QtWidgets import QLabel

    from sico_ui.records import AuditPanel
    from sico_ui.theme import ACCENT, COPILOT_STYLESHEET

    app = QApplication.instance() or QApplication([])
    assert 'QLabel[recommended="true"]' in COPILOT_STYLESHEET

    panel = AuditPanel("暂无交互记录")
    panel.setStyleSheet(COPILOT_STYLESHEET)
    row = {
        "id": "audit-1", "title": "选择 PDK", "status": "pending",
        "recommendation": "smic28hkmg",
        "questions": [{
            "id": "pdk_library", "header": "选择 PDK",
            "question": "本次电路任务使用哪个 PDK？",
            "options": [
                {"label": "smic28", "description": "/pdk/smic28"},
                {"label": "smic28hkmg (Recommended)", "description": "/pdk/smic28hkmg"},
            ],
        }],
    }
    panel.add_audit(row, {"timestamp": "2026-09-15T02:00:00+00:00"})
    panel.list.setCurrentItem(panel.list.topLevelItem(0))
    app.processEvents()

    marked = [label for label in panel.findChildren(QLabel)
              if label.property("recommended") is True]
    assert len(marked) == 1 and marked[0].text().startswith("smic28hkmg（推荐值）：")
    assert "(Recommended)" not in marked[0].text()
    others = [label for label in panel.findChildren(QLabel)
              if label.property("recommended") is not True and label.text().startswith("smic28：")]
    assert others and all(label.property("recommended") in (None, False) for label in others)

    image = marked[0].grab().toImage()
    accent = QColor(ACCENT)
    hits = sum(1 for y in range(image.height()) for x in range(image.width())
               if image.pixelColor(x, y) == accent)
    assert hits > 0, "推荐的选项没有按红棕色渲染"

    plain_image = others[0].grab().toImage()
    plain_hits = sum(1 for y in range(plain_image.height()) for x in range(plain_image.width())
                     if plain_image.pixelColor(x, y) == accent)
    assert plain_hits == 0


def audit_row_prepared():
    from sico_ui.records import AuditPanel

    app = QApplication.instance() or QApplication([])
    panel = AuditPanel("暂无交互记录")
    row = {
        "id": "audit-1", "title": "选择 PDK", "status": "pending",
        "questions": [{
            "id": "pdk_library", "header": "选择 PDK",
            "question": "本次电路任务使用哪个 PDK？",
            "options": [{"label": "smic28hkmg", "description": "/pdk/smic28hkmg"}],
        }],
    }
    panel.add_audit(row, {"timestamp": "2026-09-15T02:00:00+00:00"})
    panel.list.setCurrentItem(panel.list.topLevelItem(0))
    app.processEvents()
    return app, panel, row


def test_audit_submit_refusal_names_the_reason_instead_of_doing_nothing():
    """点击沉默会被读作“界面坏了”：拒答时必须给出原因和下一步。"""
    app, panel, row = audit_row_prepared()
    try:
        row["status"] = "withdrawn"
        panel.submit_answer()
        assert "该事项已结束或已答复" in panel.validation.text()
        assert "核对卡片" in panel.validation.text()

        panel.read_only = True
        panel.submit_answer()
        assert "历史浏览（只读）" in panel.validation.text()
    finally:
        panel.close()


def test_audit_submit_without_a_selection_explains_before_sending():
    app, panel, row = audit_row_prepared()
    try:
        panel.list.setCurrentItem(None)
        emitted = []
        panel.answerRequested.connect(lambda *_args: emitted.append(True))
        panel.submit_answer()
        assert "请先在左侧选择要答复的事项" in panel.validation.text()
        assert not emitted
    finally:
        panel.close()


def test_audit_panel_lists_only_audit_items():
    """审阅栏只收询问/确认/选择事项，不再重复对话与汇报里的记录。"""
    from sico_ui.records import AuditPanel

    app = QApplication.instance() or QApplication([])
    panel = AuditPanel("暂无交互记录")
    records = [
        {"title": "你 · 复核归档结果", "markdown": "请求正文",
         "record": {"sequence": 1, "task_id": "task"}},
        {"title": "审阅事项 · 运行方式", "markdown": "问题正文", "audit_id": "a_1",
         "record": {"sequence": 2, "task_id": "task"}},
        {"title": "Silicon Copilot", "markdown": "回答正文",
         "record": {"sequence": 3, "task_id": "task"}},
        {"title": "任务状态", "markdown": "任务已中断",
         "record": {"sequence": 4, "task_id": "task"}},
    ]
    audits = {"a_1": {"id": "a_1", "title": "运行方式", "status": "pending",
                      "questions": [{"id": "mode", "header": "运行方式",
                                     "question": "前台还是后台？", "options": []}]}}
    panel.sync_records(records, audits)
    app.processEvents()
    assert panel.list.topLevelItemCount() == 1
    item = panel.list.topLevelItem(0)
    assert item.text(1) == "审阅事项 · 运行方式"
    assert item.data(0, Qt.UserRole)["audit_id"] == "a_1"

    # 审阅被撤销/失效后，记录从列表里撤掉。
    panel.sync_records(records[:1] + records[2:], {})
    app.processEvents()
    assert panel.list.topLevelItemCount() == 0


def test_pending_audit_rows_use_the_accent_colour():
    """审阅列表里待答复的记录用红棕色字体标出，答复后恢复正常。"""
    from sico_ui.records import AuditPanel

    app = QApplication.instance() or QApplication([])
    panel = AuditPanel("暂无交互记录")
    audits = {"a_1": {"id": "a_1", "title": "运行方式", "status": "pending",
                      "questions": [{"id": "mode", "header": "运行方式",
                                     "question": "前台还是后台？", "options": []}]}}
    records = [{"title": "审阅事项 · 运行方式", "markdown": "问题正文", "audit_id": "a_1",
                "record": {"sequence": 2, "task_id": "task"}}]
    panel.sync_records(records, audits)
    app.processEvents()
    item = panel.list.topLevelItem(0)
    assert item.foreground(1).color().name() == QColor(ACCENT).name()
    audits["a_1"] = dict(audits["a_1"], status="answered")
    panel.sync_records(records, audits)
    app.processEvents()
    assert item.foreground(1).style() == Qt.NoBrush


def test_supplementary_audit_is_labeled_and_not_painted_as_blocking():
    from sico_ui.records import AuditPanel

    app = QApplication.instance() or QApplication([])
    panel = AuditPanel("暂无交互记录")
    row = {"id": "a_2", "title": "标签偏好", "status": "pending",
           "review_class": "supplementary",
           "questions": [{"id": "label", "header": "标签", "question": "用哪个标签？",
                          "options": []}]}
    panel.add_audit(row, {"timestamp": "2026-10-19T02:00:00+00:00"})
    app.processEvents()
    try:
        item = panel.list.topLevelItem(0)
        assert item.text(1) == "审阅事项 · 标签偏好 · 补充"
        assert item.foreground(1).style() == Qt.NoBrush
        row["status"] = "answered"
        row["reply"] = {"automatic": True, "answers": {}, "actor": "SiCo"}
        panel.render_audit(row["id"])
        app.processEvents()
        assert "模型按公开建议自行决定" in panel.detail.toPlainText()
    finally:
        panel.close()


def test_every_recommendation_marker_is_painted_in_the_accent_colour():
    """Real audits mix "(Recommended)", "（推荐）" and a named recommendation."""
    from PyQt5.QtGui import QColor
    from PyQt5.QtWidgets import QLabel

    from sico_ui.records import AuditPanel
    from sico_ui.theme import ACCENT, COPILOT_STYLESHEET

    app = QApplication.instance() or QApplication([])
    panel = AuditPanel("暂无交互记录")
    panel.setStyleSheet(COPILOT_STYLESHEET)
    row = {
        "id": "audit-markers", "title": "运行方式", "status": "pending",
        "recommendation": "分步执行",
        "questions": [{
            "id": "mode", "header": "运行方式", "question": "如何执行本次设计？",
            "options": [
                {"label": "前台 (Recommended)", "description": "英文后缀。"},
                {"label": "后台（推荐）", "description": "模型写的中文简写。"},
                {"label": "分步执行", "description": "由 recommendation 指定。"},
                {"label": "跳过", "description": "不推荐。"},
            ],
        }],
    }
    panel.add_audit(row, {"timestamp": "2026-09-15T02:00:00+00:00"})
    panel.list.setCurrentItem(panel.list.topLevelItem(0))
    app.processEvents()

    captions = {label.text(): label for label in panel.findChildren(QLabel)
                if label.property("recommended") is True}
    assert set(captions) == {
        "前台（推荐值）：英文后缀。",
        "后台（推荐值）：模型写的中文简写。",
        "分步执行：由 recommendation 指定。",
    }
    accent = QColor(ACCENT)
    for text, label in captions.items():
        image = label.grab().toImage()
        assert any(image.pixelColor(x, y) == accent
                   for y in range(image.height()) for x in range(image.width())), text
    plain = next(label for label in panel.findChildren(QLabel) if label.text().startswith("跳过："))
    plain_image = plain.grab().toImage()
    assert not any(plain_image.pixelColor(x, y) == accent
                   for y in range(plain_image.height()) for x in range(plain_image.width()))


def test_send_button_recovers_when_the_audit_projection_settles(tmp_path):
    """答复交付后，即使会话快照还停着旧任务，按钮也要回到“发送”。"""
    from dataclasses import replace

    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "send-button") as journal:
        loop = PythonBackend(
            ContextDemoProvider(), ToolRegistry(), journal,
            BoundContext("send-instance", "send-generation", "bound", {"valid": True}),
        )
        loop._event("session.created", {"entry": "test"})
        window = AssistantWindow(loop)
        settle(window)
        try:
            audits = {}
            window.centers.index = replace(window.centers.index, audits=audits)
            state = dict(window.presentation.state or {})
            state.update(busy=True, closing=False, fault="", pending=0, paused=False, requests=[])
            state.setdefault("context", {
                "instance_id": "send-instance", "generation": "send-generation",
                "target_id": "bound", "snapshot": {},
            })
            state["task"] = {"id": "task-1", "status": "executing", "waiting_audits": ["a_1"]}

            # 审阅投影还没见过这个 id：沿用会话快照，按钮是“答复问题”。
            window.renderer.update_session(state)
            assert window.send.text() == "答复问题"
            assert window.actions.open_audit_ids(state) == ["a_1"]

            # 投影里已 resumed（status=answered）：按钮立刻恢复，不再等任务事件。
            audits["a_1"] = {"id": "a_1", "task_id": "task-1", "status": "answered"}
            window.centers.index = replace(window.centers.index, audits=audits)
            window.renderer.update_session(state)
            assert window.actions.open_audit_ids(state) == []
            assert window.send.text() == "发送"
            assert not window.send.isEnabled()
            assert "当前任务完成后执行" in window.send.toolTip()
            assert window.steering_input.menu_button.isEnabled()

            # 投影显示仍在等待答复：保持“答复问题”。
            audits["a_1"] = {"id": "a_1", "task_id": "task-1", "status": "pending"}
            window.centers.index = replace(window.centers.index, audits=audits)
            window.renderer.update_session(state)
            assert window.actions.open_audit_ids(state) == ["a_1"]
            assert window.send.text() == "答复问题"
            app.processEvents()
        finally:
            window.close()


def test_audit_default_split_is_one_to_two(tmp_path):
    app = QApplication.instance() or QApplication([])
    with SessionJournal(tmp_path, "audit-split") as journal:
        window = AssistantWindow(
            PythonBackend(ContextDemoProvider(), ToolRegistry(), journal,
                      BoundContext("i", "g", "bound", {"valid": True}))
        )
        window.resize(1000, 700)
        window.show()
        try:
            app.processEvents()
            audit = window.centers.audit
            sizes = audit.sizes()
            assert sizes[0] > 0 and sizes[1] > 0 and sizes[2] == 0
            assert abs(sizes[1] / sizes[0] - 2) < 0.15
        finally:
            window.close()


AUDIT_ANSWER_ROW = {
    "id": "audit-1", "title": "选择 PDK", "status": "pending",
    "recommendation": "smic28hkmg",
    "questions": [{
        "id": "pdk_library", "header": "选择 PDK",
        "question": "本次电路任务使用哪个 PDK？",
        "options": [
            {"label": "smic28", "description": "/pdk/smic28"},
            {"label": "smic28hkmg (Recommended)", "description": "/pdk/smic28hkmg"},
        ],
    }],
}


def open_answer_form():
    """审阅面板里打开唯一一条待答复事项，返回面板和它的选项圆点。"""
    from PyQt5.QtWidgets import QRadioButton

    from sico_ui.records import AuditPanel
    from sico_ui.theme import COPILOT_STYLESHEET

    app = QApplication.instance() or QApplication([])
    panel = AuditPanel("暂无交互记录")
    panel.setStyleSheet(COPILOT_STYLESHEET)
    panel.add_audit(AUDIT_ANSWER_ROW, {"timestamp": "2026-09-22T02:00:00+00:00"})
    panel.list.setCurrentItem(panel.list.topLevelItem(0))
    app.processEvents()
    return panel, panel.choices.findChildren(QRadioButton)


def test_audit_option_dots_use_the_accent_colour():
    """选项圆点整体红棕色：空心圆是描边，选中时圆心补上同色实心点。"""
    from PyQt5.QtGui import QColor

    from sico_ui.theme import ACCENT

    app = QApplication.instance() or QApplication([])
    panel, dots = open_answer_form()
    accent = QColor(ACCENT)

    def marked(dot):
        image = dot.grab().toImage()
        return {(x, y) for y in range(image.height()) for x in range(image.width())
                if image.pixelColor(x, y) == accent}

    try:
        assert len(dots) == 2 and not any(dot.isChecked() for dot in dots)
        first, second = dots
        ring = marked(first)
        assert ring, "未选中的选项圆点没有红棕描边"
        xs = [position[0] for position in ring]
        ys = [position[1] for position in ring]
        center = ((min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2)
        assert center not in ring, "未选中的圆点必须是空心的"
        assert center not in marked(second)

        second.setChecked(True)
        app.processEvents()
        filled = marked(second)
        assert center in filled, "选中的圆点圆心不是红棕色实心点"
        assert len(filled) > len(ring)
    finally:
        panel.close()


def test_audit_answer_button_wears_the_send_action_fill():
    """“提交答复”和“发送”同一色彩风格：红棕底、浅色字。"""
    from PyQt5.QtGui import QColor

    from sico_ui.records import AuditPanel
    from sico_ui.theme import ACCENT, COPILOT_STYLESHEET

    app = QApplication.instance() or QApplication([])
    panel = AuditPanel("暂无交互记录")
    panel.setStyleSheet(COPILOT_STYLESHEET)
    panel.show()
    try:
        app.processEvents()
        button = panel.answer
        assert button.text() == "提交答复" and button.property("bottomAction") is True
        image = button.grab().toImage()
        accent = QColor(ACCENT)
        filled = sum(1 for y in range(image.height()) for x in range(image.width())
                     if image.pixelColor(x, y) == accent)
        assert filled > image.width() * image.height() // 2
    finally:
        panel.close()


def test_audit_answers_use_return_to_submit():
    from PyQt5.QtGui import QTextCursor
    from PyQt5.QtTest import QTest

    app = QApplication.instance() or QApplication([])
    panel, dots = open_answer_form()
    replies = []
    panel.answerRequested.connect(lambda audit_id, answers: replies.append((audit_id, answers)))
    try:
        dots[1].setChecked(True)
        edit = panel.text_edits["pdk_library"]
        edit.setPlainText("Vin 1.8 V")
        edit.moveCursor(QTextCursor.End)

        QTest.keyClick(edit, Qt.Key_Return)
        app.processEvents()
        assert replies == [("audit-1", {"pdk_library": {
            "choice": "smic28hkmg (Recommended)", "text": "Vin 1.8 V"}})]

        # Shift+回车留给答复正文换行，不提交。
        replies.clear()
        QTest.keyClick(edit, Qt.Key_Return, Qt.ShiftModifier)
        app.processEvents()
        assert not replies and edit.toPlainText() == "Vin 1.8 V\n"

        # 焦点在选项圆点上时，回车同样等于“提交答复”。
        QTest.keyClick(dots[1], Qt.Key_Return)
        app.processEvents()
        assert [audit_id for audit_id, _ in replies] == ["audit-1"]
    finally:
        panel.close()
