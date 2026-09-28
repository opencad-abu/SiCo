"""Compact bounded record list with passive document and field details."""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QTextDocument
from PyQt5.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .input import SendOnReturnEdit, SendOnReturnRadioButton
from .presentation import (
    DocumentView,
    data_html,
    event_time,
    is_recommended,
    plain_recommendation,
    recommended_label,
    style_tables,
)


class RecordPanel(QSplitter):
    def __init__(self, empty):
        super().__init__(Qt.Vertical)
        self.empty = empty
        self.list = QTreeWidget()
        self.list.setHeaderLabels(["时间", "记录"])
        self.list.setRootIsDecorated(False)
        self.list.setVerticalScrollMode(QTreeWidget.ScrollPerPixel)
        self.list.header().setStretchLastSection(False)
        self.list.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.list.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.detail = DocumentView()
        self.detail.setOpenLinks(False)
        self.detail.setOpenExternalLinks(False)
        self.detail.setPlainText(empty)
        self.list.currentItemChanged.connect(self.select)
        self.addWidget(self.list)
        self.addWidget(self.detail)
        self.setSizes([160, 400])

    def clear(self):
        self.list.clear()
        self.detail.setPlainText(self.empty)

    def add(self, title, record, *, markdown=None):
        count = self.list.topLevelItemCount()
        follow = not count or self.list.currentItem() is self.list.topLevelItem(count - 1)
        item = QTreeWidgetItem([self.record_time(record), title[:200]])
        item.setToolTip(1, title)
        item.setData(0, Qt.UserRole, {"record": record, "markdown": markdown})
        self.list.addTopLevelItem(item)
        if count >= 200:
            self.list.takeTopLevelItem(0)
        if follow:
            self.list.setCurrentItem(item)
        return item

    @staticmethod
    def record_time(record):
        """Local timestamp of a record, for the dedicated time column."""
        if not isinstance(record, dict):
            return ""
        return event_time(record.get("timestamp") or record.get("updated_at"))

    def select(self, item, previous=None):
        if item is None:
            self.detail.setPlainText(self.empty)
            return
        data = item.data(0, Qt.UserRole)
        record = data["record"]
        if data["markdown"] is not None:
            self.detail.document().setMarkdown(
                data["markdown"][:100000],
                QTextDocument.MarkdownFeatures(
                    QTextDocument.MarkdownDialectGitHub | QTextDocument.MarkdownNoHTML
                ),
            )
        else:
            self.detail.setHtml(data_html(record))
        style_tables(self.detail.document())
        self.detail.setToolTip(
            f"会话：{record.get('session_id', '')}\n任务：{record.get('task_id', '')}"
        )


class AuditPanel(RecordPanel):
    """Public audit records with one shared answer form for live sessions."""

    answerRequested = pyqtSignal(str, object)
    elicitationRequested = pyqtSignal(object, object)

    def __init__(self, empty):
        super().__init__(empty)
        self.audit_rows = {}
        self.text_edits = {}
        self.editing_audit = None
        self.read_only = False
        self.editor = QWidget()
        form = QVBoxLayout(self.editor)
        form.setContentsMargins(8, 6, 8, 6)
        form.setSizeConstraint(QLayout.SetMinimumSize)
        form.setAlignment(Qt.AlignTop)
        self.prompt = QLabel()
        self.prompt.setWordWrap(True)
        form.addWidget(self.prompt)
        self.validation = QLabel()
        self.validation.setTextFormat(Qt.PlainText)
        self.validation.setWordWrap(True)
        self.validation.hide()
        form.addWidget(self.validation)
        self.choices = QWidget()
        # The form is taller than the questions in a normal side pane. Keep
        # this group at its size hint so each question stays at the top and
        # spare height remains below the answer controls.
        self.choices.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.choice_layout = QVBoxLayout(self.choices)
        self.choice_layout.setContentsMargins(0, 0, 0, 0)
        self.choice_layout.setSizeConstraint(QLayout.SetMinimumSize)
        self.choice_layout.setAlignment(Qt.AlignTop)
        form.addWidget(self.choices)
        actions = QHBoxLayout()
        self.answer = QPushButton("提交答复")
        # 审阅页面的主操作和发送按钮同一强调色填充（bottomAction）。
        self.answer.setProperty("bottomAction", True)
        self.answer.clicked.connect(self.submit_answer)
        actions.addWidget(self.answer)
        # Keep the action anchored to the left so long questions cannot push it
        # out of the visible side pane.
        actions.addStretch(1)
        form.addLayout(actions)
        self.editor_scroll = QScrollArea()
        self.editor_scroll.setWidgetResizable(True)
        # Keep the answer form anchored at the top of the available pane.
        # The scroll area then leaves any extra height below the questions,
        # instead of distributing it between the prompt and the choices.
        self.editor_scroll.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        from .elicitation import ElicitationForm, InteractionEditors

        self.elicitation_form = ElicitationForm()
        self.elicitation_form.requested.connect(self.elicitationRequested)
        self.editor_stack = InteractionEditors()
        self.editor_stack.addWidget(self.editor)
        self.editor_stack.addWidget(self.elicitation_form)
        self.editor_scroll.setWidget(self.editor_stack)
        self.editor_scroll.hide()
        self.addWidget(self.editor_scroll)
        # The audit list and its detail pane use a 1:2 default vertical split.
        # Relative values are intentional here: Qt scales them when the dock
        # receives its actual height, and the hidden answer editor remains at
        # zero until an audit requires it.
        self.setSizes([1, 2, 0])

    def clear(self):
        super().clear()
        self.elicitation_form.setEnabled(False)
        self.elicitation_form.row = None
        self.audit_rows = {}
        self.editing_audit = None
        self.text_edits = {}
        self.editor_scroll.hide()

    def sync_records(self, records, audits):
        """Apply a projection without clearing the active answer form or draft."""
        previous = self.list.currentItem()
        old_data = previous.data(0, Qt.UserRole) if previous else None
        old_audit = self.audit_rows.get((old_data or {}).get("audit_id"))
        count = self.list.topLevelItemCount()
        follow = not count or previous is self.list.topLevelItem(count - 1)
        items = {
            self.list.topLevelItem(n).data(0, Qt.UserRole)["record"]["sequence"]:
            self.list.topLevelItem(n)
            for n in range(count)
        }
        self.audit_rows = audits
        self.list.blockSignals(True)
        try:
            for entry in records:
                row = audits.get(entry.get("audit_id"))
                if row is None:
                    # 审阅栏只收询问/确认/选择事项；其余记录留在对话正文与汇报栏，
                    # 否则同一句话在这里和汇报里各出现一次。
                    continue
                record = entry["record"]
                data = {"record": record, "markdown": self.markdown(row), "audit_id": row["id"]}
                title = "审阅事项 · " + row["title"]
                item = items.pop(record["sequence"], None)
                if item is None:
                    item = QTreeWidgetItem()
                    self.list.addTopLevelItem(item)
                    if follow:
                        self.list.setCurrentItem(item)
                item.setText(0, self.record_time(record))
                item.setText(1, title[:200])
                item.setToolTip(1, title)
                item.setData(0, Qt.UserRole, data)
            for item in items.values():
                self.list.takeTopLevelItem(self.list.indexOfTopLevelItem(item))
        finally:
            self.list.blockSignals(False)
        current = self.list.currentItem()
        data = current.data(0, Qt.UserRole) if current else None
        if (current is not previous or data != old_data or
                audits.get((data or {}).get("audit_id")) != old_audit):
            self.select(current)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.editor_scroll.isHidden():
            self._set_default_split()

    def _set_default_split(self):
        """Keep the audit list/detail panes at a one-to-two height ratio."""
        available = max(3, self.height())
        self.setSizes([available // 3, (available * 2) // 3, 0])

    def add_audit(self, row, event=None):
        self.audit_rows[row["id"]] = row
        item = self.add(
            "审阅事项 · " + row["title"],
            event or row,
            markdown=self.markdown(row),
        )
        item.setData(0, Qt.UserRole, {"record": event or row, "markdown": self.markdown(row),
                                      "audit_id": row["id"]})
        if self.list.currentItem() is item:
            self.select(item)
        return item

    @staticmethod
    def markdown(row):
        text = "## " + row["title"] + "\n\n"
        if "elicitation" in row:
            from sico.codex.elicitation_schema import status_text

            text += status_text(row) + "\n\n"
            if row["elicitation"]["mode"] == "url":
                text += row["elicitation"]["url"] + "\n\n"
        if row.get("recommendation"):
            text += "**建议**：" + row["recommendation"] + "\n\n"
        if row.get("rationale"):
            text += "**依据**：" + row["rationale"] + "\n\n"
        if row.get("review_markdown"):
            text += row["review_markdown"] + "\n\n"
        text += "\n\n".join(f"**{q['header']}**：{q['question']}" for q in row["questions"])
        if row.get("reason"):
            text += "\n\n" + row["reason"]
        if row.get("reply"):
            if "elicitation" in row:
                from sico.codex.elicitation_schema import reply_text

                return text + "\n\n" + reply_text(row, row["reply"])
            text += "\n\n**用户答复**：" + "；".join(
                ", ".join(v for v in (recommended_label(a.get("choice", "")), a.get("text", "")) if v)
                for a in row["reply"]["answers"].values()
            )
        return text

    def select(self, item, previous=None):
        self.elicitation_form.setEnabled(False)
        super().select(item, previous)
        self.detail.show()
        if item is None:
            self.editor_scroll.hide()
            return
        audit_id = item.data(0, Qt.UserRole).get("audit_id")
        row = self.audit_rows.get(audit_id)
        if not row or row.get("status") != "pending" or self.read_only:
            was_editing = not self.editor_scroll.isHidden()
            self.editor_scroll.hide()
            if was_editing:
                self._set_default_split()
            return
        if "elicitation" in row:
            self.elicitation_form.show_row(row)
            self.elicitation_form.setEnabled(True)
            self.editor_stack.setCurrentWidget(self.elicitation_form)
            self.detail.hide()
            self.editor_scroll.show()
            self.setSizes([100, 0, max(180, self.height() - 100)])
            return
        self.editor_stack.setCurrentWidget(self.editor)
        self.prompt.setText(row["title"])
        self.prompt.setTextFormat(Qt.PlainText)
        drafts = ({key: edit.toPlainText() for key, edit in self.text_edits.items()}
                  if self.editing_audit == audit_id else {})
        selected = {button.property("question_id"): button.property("choice")
                    for button in self.choices.findChildren(QRadioButton)
                    if button.isChecked() and self.editing_audit == audit_id}
        self.editing_audit = audit_id
        self.validation.hide()
        for group in self.findChildren(QButtonGroup):
            group.deleteLater()
        while self.choice_layout.count():
            child = self.choice_layout.takeAt(0)
            widget = child.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        self.text_edits = {}
        for question in row["questions"]:
            label = QLabel(question["header"] + "：" + question["question"])
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
            self.choice_layout.addWidget(label)
            group = QButtonGroup(self)
            group.setObjectName(question["id"])
            for option in question["options"]:
                button = SendOnReturnRadioButton()
                button.setAccessibleName(option["label"])
                button.setToolTip(option["label"] + "：" + option["description"])
                button.setProperty("question_id", question["id"])
                button.setProperty("choice", option["label"])
                group.addButton(button)
                button.setChecked(selected.get(question["id"]) == option["label"])
                button.submitted.connect(self.submit_answer)
                line = QWidget()
                line.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
                option_layout = QHBoxLayout(line)
                option_layout.setContentsMargins(0, 0, 0, 0)
                option_layout.addWidget(button, 0, Qt.AlignTop)
                caption = QLabel(
                    recommended_label(option["label"]) + "：" + option["description"]
                )
                caption.setBuddy(button)
                caption.setTextFormat(Qt.PlainText)
                caption.setWordWrap(True)
                if self.recommended_option(option["label"], row):
                    button.setProperty("recommended", True)
                    caption.setProperty("recommended", True)
                option_layout.addWidget(caption, 1, Qt.AlignTop)
                self.choice_layout.addWidget(line)
            edit = SendOnReturnEdit()
            edit.setPlaceholderText("答复或补充说明；Enter 提交答复，Shift+Enter 换行")
            edit.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
            edit.setFixedHeight(60)
            edit.setPlainText(drafts.get(question["id"], ""))
            edit.submitted.connect(self.submit_answer)
            self.text_edits[question["id"]] = edit
            self.choice_layout.addWidget(edit)
            edit.setVisible("native_approval" not in row)
        self.answer.setEnabled(True)
        show_detail = "native_approval" in row or bool(row.get("review_markdown"))
        self.detail.setVisible(show_detail)
        self.editor.adjustSize()
        self.editor_scroll.show()
        if show_detail:
            self.setSizes([80, max(220, (self.height() - 80) // 2),
                           max(240, (self.height() - 80) // 2)])
        else:
            self.setSizes([100, 0, max(180, self.height() - 100)])

    @staticmethod
    def recommended_option(label, row):
        """Host-preferred choice: marked by suffix or by the audit recommendation."""
        if is_recommended(label):
            return True
        recommendation = row.get("recommendation") if isinstance(row, dict) else ""
        if not recommendation:
            return False
        # Compare without markers so "后台" and "后台（推荐）" name the same choice.
        return plain_recommendation(recommendation) == plain_recommendation(label)

    def submit_answer(self, _checked=False):
        """Qt calls clicked(bool); the shipped Cython build forwards it."""
        item = self.list.currentItem()
        if item is None:
            return
        audit_id = item.data(0, Qt.UserRole).get("audit_id")
        row = self.audit_rows.get(audit_id)
        if not row or row.get("status") != "pending" or self.read_only:
            return
        answers = {}
        missing = []
        for question in row["questions"]:
            choice = ""
            for button in self.choices.findChildren(QRadioButton):
                if button.isChecked() and button.property("question_id") == question["id"]:
                    choice = button.property("choice")
                    break
            answers[question["id"]] = {
                "choice": choice,
                "text": self.text_edits[question["id"]].toPlainText(),
            }
            if not choice and not answers[question["id"]]["text"].strip():
                missing.append(question["header"])
        if missing:
            self.show_error("尚未答复：" + "、".join(missing))
            return
        self.validation.hide()
        self.answerRequested.emit(audit_id, answers)

    def show_error(self, message):
        self.validation.setText(message)
        self.validation.show()
        self.editor_scroll.ensureWidgetVisible(self.validation)

    def render_audit(self, audit_id):
        row = self.audit_rows.get(audit_id)
        if row is None:
            return
        for n in range(self.list.topLevelItemCount()):
            item = self.list.topLevelItem(n)
            if item.data(0, Qt.UserRole).get("audit_id") == audit_id:
                item.setText(1, "审阅事项 · " + row["title"])
                item.setToolTip(1, "审阅事项 · " + row["title"])
                old = item.data(0, Qt.UserRole)
                item.setData(
                    0,
                    Qt.UserRole,
                    {
                        "record": old.get("record", row),
                        "markdown": self.markdown(row),
                        "audit_id": audit_id,
                    },
                )
                if self.list.currentItem() is item:
                    self.select(item)
                else:
                    self.list.setCurrentItem(item)
                return
