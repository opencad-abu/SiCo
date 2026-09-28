"""Inputs where Return submits the current reply and Shift+Return keeps its newline."""

from __future__ import annotations

from copy import deepcopy

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QRadioButton

MAX_INPUT_CHARS = 16000


def submit_on_return(widget, event):
    """回车提交当前答复；返回 False 表示该键留给控件自己处理。

    Shift+Return 永远返回 False：多行编辑器用它换行，单行输入框和选项圆点
    则和 Qt 默认行为一致地忽略它。提交前先落定输入法预编辑，回车读到的必须
    是已上屏的文字。
    """
    if (event.key() not in (Qt.Key_Return, Qt.Key_Enter)
            or event.modifiers() & Qt.ShiftModifier):
        return False
    QApplication.inputMethod().commit()
    event.accept()
    widget.submitted.emit()
    return True


class SendOnReturnEdit(QPlainTextEdit):
    """A multiline editor where Return submits and Shift+Return inserts a line.

    Keeping this behavior in the editor means Return only submits while the
    message field has focus.  Other widgets keep their normal keyboard
    behavior, and Shift+Return is passed to Qt so it inserts a newline.
    """

    submitted = pyqtSignal()
    attachmentPasted = pyqtSignal()

    def __init__(self, parent=None, *, attachments_enabled=False):
        super().__init__(parent)
        self.attachments_enabled = attachments_enabled
        self._text_attachment = None
        self._changing_attachment = False
        self.turn_inputs = []
        self.turn_options = None
        self.draft_revision = 0
        self.textChanged.connect(self._revise_draft)

    def _revise_draft(self, *_args):
        """QTextEdit.textChanged(str) forwards the text; the shipped Cython build passes it."""
        self.draft_revision += 1

    def insertFromMimeData(self, source):
        if not self.attachments_enabled or not source.hasText():
            super().insertFromMimeData(source)
            return
        incoming = source.text()
        cursor = self.textCursor()
        current = self.toPlainText()
        replacement = (current[: cursor.selectionStart()] + incoming
                       + current[cursor.selectionEnd() :])
        if len(replacement) <= MAX_INPUT_CHARS:
            self._text_attachment = None
            super().insertFromMimeData(source)
            return
        # Keep the editor bounded and retain the complete clipboard payload for
        # the send action.  The visible draft before the selection is preserved.
        self._text_attachment = incoming
        self._revise_draft()
        # ``QTextCursor.insertText("")`` is not a reliable way to undo a
        # paste: Qt has already applied the MIME insertion by the time this
        # virtual method is reached.  Restore the exact pre-paste document and
        # selection explicitly, so an existing draft is never changed by the
        # attachment conversion.
        selection_start = cursor.selectionStart()
        selection_end = cursor.selectionEnd()
        position = cursor.position()
        self._changing_attachment = True
        try:
            self.setPlainText(current)
            restored = self.textCursor()
            if selection_start != selection_end:
                restored.setPosition(selection_start)
                restored.setPosition(selection_end, restored.KeepAnchor)
            else:
                restored.setPosition(position)
            self.setTextCursor(restored)
        finally:
            self._changing_attachment = False
        self.attachmentPasted.emit()

    def keyPressEvent(self, event):
        if not submit_on_return(self, event):
            super().keyPressEvent(event)

    def text_attachment(self):
        return self._text_attachment

    def clear_text_attachment(self):
        self._text_attachment = None
        self._revise_draft()

    def draft(self):
        cursor = self.textCursor()
        return (self.toPlainText(), self._text_attachment, cursor.anchor(), cursor.position(),
                deepcopy(self.turn_inputs), deepcopy(self.turn_options))

    def restore_draft(self, draft):
        text, attachment, anchor, position = draft[:4]
        self.turn_inputs = deepcopy(draft[4]) if len(draft) > 4 else []
        self.turn_options = deepcopy(draft[5]) if len(draft) > 5 else None
        self.setPlainText(text)
        self._text_attachment = attachment
        self._revise_draft()
        cursor = self.textCursor()
        cursor.setPosition(anchor)
        cursor.setPosition(position, cursor.KeepAnchor)
        self.setTextCursor(cursor)

    def draft_content(self):
        draft = self.draft()
        return draft[:2] + draft[4:]

    def clear_submission(self):
        self.clear()
        self.clear_text_attachment()
        self.turn_inputs = []
        self.turn_options = None


class SendOnReturnLineEdit(QLineEdit):
    """A single-line field where Return submits instead of doing nothing."""

    submitted = pyqtSignal()

    def keyPressEvent(self, event):
        if not submit_on_return(self, event):
            super().keyPressEvent(event)


class SendOnReturnRadioButton(QRadioButton):
    """A choice dot where Return answers the question its group belongs to."""

    submitted = pyqtSignal()

    def keyPressEvent(self, event):
        if not submit_on_return(self, event):
            super().keyPressEvent(event)
