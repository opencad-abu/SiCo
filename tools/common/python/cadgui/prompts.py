"""Family dialogs: frameless confirm, input and file prompts shared by the flows.

所有流程的 QMessageBox / QInputDialog / 文件选择统一由这里替代：同样的无边框窗口、
自绘标题栏和红棕主题，按钮顺序就是调用方注册的顺序，不再跟随平台的对话框布局约定。
文件选择用 Qt 的非原生选择器嵌进自绘窗口，浏览、过滤和最近路径仍然来自 Qt。
"""

from __future__ import annotations

import os

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .chrome import SiDialog

# 三个常用按钮名：措辞只在这里定义，调用点不再各写一套。
CONFIRM = "确定"
CANCEL = "取消"
ACKNOWLEDGE = "知道了"


class SiForm(SiDialog):
    """确认框和输入框共用的版式：说明文字、内容区、右对齐的动作行。"""

    controls = ("close",)
    chosen = pyqtSignal(str)

    def __init__(self, title, parent=None):
        super().__init__(title, parent, self.controls)
        self.setWindowModality(Qt.WindowModal)
        self.result_key = None
        self._escape = None
        self.body_layout.setContentsMargins(16, 14, 16, 14)
        self.body_layout.setSpacing(12)
        self.message = QLabel()
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        self.body_layout.addWidget(self.message)
        self.fields = QVBoxLayout()
        self.fields.setSpacing(8)
        self.body_layout.addLayout(self.fields)
        self._buttons = {}
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.actions.addStretch(1)

    def set_message(self, text):
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def add_field(self, widget):
        self.fields.addWidget(widget)
        return widget

    def add_choice(self, key, text, *, default=False, escape=False, danger=False):
        """注册一个按钮，返回它本身；按钮宽度随文案自适应。"""
        button = QPushButton(text)
        button.setAutoDefault(False)
        if danger:
            button.setProperty("dialogDanger", True)
        button.clicked.connect(lambda _checked=False, value=key: self.pick(value))
        self._buttons[key] = button
        self.actions.addWidget(button)
        if default:
            button.setDefault(True)
        if escape:
            self._escape = key
        return button

    def button(self, key):
        return self._buttons[key]

    def set_enabled(self, key, enabled):
        """按 key 启用/禁用一个动作，调用方不需要碰按钮本身。"""
        self.button(key).setEnabled(enabled)

    def set_default(self, key):
        """把默认动作换成另一个：Enter 跟随它，焦点也移过去。"""
        for name, button in self._buttons.items():
            button.setDefault(name == key)
        self.button(key).setFocus()

    def pick(self, key):
        """记下一次选择：发出 chosen 并关闭对话框；重复选择只算第一次。"""
        if self.result_key is not None:
            return
        self.result_key = key
        self.chosen.emit(key)
        self.accept()

    def ask(self):
        """阻塞到用户选择，返回被选按钮的 key；标题栏关闭等同 escape 按钮。"""
        self.exec_()
        return self.result_key

    def reject(self):
        if self.result_key is None and self._escape is not None:
            self.pick(self._escape)
            return
        super().reject()


def _finish(form):
    """把动作行挂到版式末尾，返回同一个对话框。"""
    form.body_layout.addLayout(form.actions)
    return form


class SiConfirm(SiForm):
    """一条正文加若干按钮；按钮从左到右就是注册顺序。"""

    def __init__(self, title, text, parent=None):
        super().__init__(title, parent)
        self.set_message(text)
        _finish(self)


class SiPrompt(SiForm):
    """一行说明加一个输入控件的输入框。"""

    def __init__(self, title, label, field, parent=None):
        super().__init__(title, parent)
        self.set_message(label)
        self.field = self.add_field(field)
        self.add_choice("accept", CONFIRM, default=True)
        self.add_choice("cancel", CANCEL, escape=True)
        _finish(self)

    def value(self):
        if isinstance(self.field, QComboBox):
            return self.field.currentText()
        if isinstance(self.field, QPlainTextEdit):
            return self.field.toPlainText()
        return self.field.text()

    def ask(self):
        """阻塞到用户选择，返回 (文本, 是否确认)。"""
        accepted = super().ask() == "accept"
        return self.value(), accepted

    def showEvent(self, event):
        super().showEvent(event)
        self.field.setFocus()


class SiFilePrompt(SiForm):
    """文件选择：Qt 的非原生选择器嵌进自绘窗口，窗口边框和标题栏仍是 SiCo 的。"""

    def __init__(self, title, filters, parent=None, *, directory="", mode=QFileDialog.ExistingFile):
        super().__init__(title, parent)
        self.picker = QFileDialog(self)
        self.picker.setOption(QFileDialog.DontUseNativeDialog, True)
        self.picker.setFileMode(mode)
        self.picker.setNameFilters([filter for filter in filters if filter])
        if directory:
            self.picker.setDirectory(directory)
        # 选择器自己的打开/取消和这里的按钮重复，只保留自绘的那一排。
        self.picker.setWindowFlags(Qt.Widget)
        self.add_field(self.picker)
        self.add_choice("accept", CONFIRM, default=True)
        self.add_choice("cancel", CANCEL, escape=True)
        _finish(self)
        self.resize(760, 520)
        self.picker.accepted.connect(lambda: self.pick("accept"))
        self.picker.rejected.connect(lambda: self.pick("cancel"))

    def showEvent(self, event):
        # 选择器显示后才建好自己的按钮行：那时再藏，避免和自绘按钮重复。
        super().showEvent(event)
        for box in self.picker.findChildren(QDialogButtonBox):
            box.hide()

    def value(self):
        # 没选文件时 Qt 会把当前目录当结果返回：只认真正存在的文件。
        selected = self.picker.selectedFiles()
        path = selected[0] if selected else ""
        return path if path and os.path.isfile(path) else ""

    def values(self):
        """多选时的全部文件；保存模式允许一个还不存在的路径。"""

        selected = [path for path in self.picker.selectedFiles() if path]
        if self.picker.fileMode() == QFileDialog.AnyFile:
            return selected
        return [path for path in selected if os.path.isfile(path)]

    def ask(self):
        """阻塞到用户选择，返回 (路径, 是否确认)；没选中文件就不算确认。"""
        accepted = super().ask() == "accept"
        if self.picker.acceptMode() == QFileDialog.AcceptSave:
            paths = self.values()
            return (paths[0] if paths else ""), accepted and bool(paths)
        path = self.value()
        return path, accepted and bool(path)


def confirm(parent, title, text, *, choice, cancel=CANCEL, danger=False):
    """两个选择的确认框；返回是否点了确认按钮。"""
    dialog = SiConfirm(title, text, parent)
    dialog.add_choice("accept", choice, default=True, danger=danger)
    dialog.add_choice("cancel", cancel, escape=True)
    return dialog.ask() == "accept"


def notice(parent, title, text, *, button=ACKNOWLEDGE):
    """单按钮提示：启动失败这类必须让用户看过再继续的消息。"""
    dialog = SiConfirm(title, text, parent)
    dialog.add_choice("ok", button, default=True, escape=True)
    return dialog.ask()


def ask_text(parent, title, label, *, value="", password=False):
    """单行输入；返回 (文本, 是否确认)。"""
    field = QLineEdit(value)
    if password:
        field.setEchoMode(QLineEdit.Password)
    else:
        field.selectAll()
    return SiPrompt(title, label, field, parent).ask()


def ask_multiline(parent, title, label, *, value=""):
    """多行输入；返回 (文本, 是否确认)。"""
    field = QPlainTextEdit(value)
    field.setMinimumSize(420, 160)
    return SiPrompt(title, label, field, parent).ask()


def ask_choice(parent, title, label, items, *, current=None):
    """从给定条目里选一个；返回 (文本, 是否确认)。"""
    field = QComboBox()
    field.addItems([str(item) for item in items])
    if current is not None:
        field.setCurrentText(str(current))
    return SiPrompt(title, label, field, parent).ask()


def ask_file(parent, title, filters, *, directory=""):
    """选择一个已存在的文件；返回 (路径, 是否确认)。"""
    return SiFilePrompt(title, filters, parent, directory=directory).ask()


def ask_files(parent, title, filters, *, directory=""):
    """选择多个已存在的文件；返回 (路径列表, 是否确认)。"""
    dialog = SiFilePrompt(title, filters, parent, directory=directory,
                          mode=QFileDialog.ExistingFiles)
    accepted = dialog.ask() == "accept"
    paths = dialog.values()
    return paths, accepted and bool(paths)


def ask_save_file(parent, title, filters, *, directory=""):
    """选择一个写入路径；返回 (路径, 是否确认)。"""
    dialog = SiFilePrompt(title, filters, parent, directory=directory,
                          mode=QFileDialog.AnyFile)
    dialog.picker.setAcceptMode(QFileDialog.AcceptSave)
    accepted = dialog.ask() == "accept"
    paths = dialog.values()
    return (paths[0] if paths else ""), accepted and bool(paths)
