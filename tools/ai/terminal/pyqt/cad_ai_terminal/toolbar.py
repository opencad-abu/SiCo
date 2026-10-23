"""Terminal toolbar controls with explicit action callbacks."""

from __future__ import annotations

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtWidgets import QLabel, QLineEdit, QStyle, QToolBar

from cadgui.palette import active


class TerminalToolbar(QToolBar):
    def __init__(
        self,
        parent,
        workspace,
        *,
        copy,
        paste,
        find,
        zoom_out_callback,
        zoom_in_callback,
        interrupt_callback,
    ) -> None:
        super().__init__("终端", parent)
        self.setObjectName("terminalToolbar")
        self.setMovable(False)
        self.setFloatable(False)
        self.setIconSize(QSize(18, 18))

        self._status = QLabel(" 启动中 ", self)
        self._status.setObjectName("sessionStatus")
        self._status.setMinimumWidth(82)
        self._status.setAlignment(Qt.AlignCenter)
        self.addWidget(self._status)
        self.addSeparator()
        workspace_label = QLabel("工作区：", self)
        workspace_label.setObjectName("workspaceLabel")
        workspace_label.setContentsMargins(6, 0, 6, 0)
        self.addWidget(workspace_label)
        workspace_field = QLineEdit(str(workspace), self)
        workspace_field.setObjectName("workspacePath")
        workspace_field.setReadOnly(True)
        workspace_field.setMinimumWidth(240)
        workspace_field.setMaximumWidth(460)
        workspace_field.setToolTip(str(workspace))
        self.addWidget(workspace_field)
        self.addSeparator()

        self._copy = self._tool_action(
            "edit-copy",
            QStyle.SP_FileDialogDetailedView,
            "复制选中内容",
            QKeySequence("Ctrl+Shift+C"),
        )
        self._copy.setObjectName("copyAction")
        self._copy.setText("复制")
        self._copy.setEnabled(False)
        self._paste = self._tool_action(
            "edit-paste",
            QStyle.SP_DialogOpenButton,
            "粘贴剪贴板",
            QKeySequence("Ctrl+Shift+V"),
        )
        self._paste.setObjectName("pasteAction")
        self._paste.setText("粘贴")
        self._paste.setEnabled(False)
        self._find = self._tool_action(
            "edit-find",
            QStyle.SP_FileDialogContentsView,
            "搜索终端",
            QKeySequence("Ctrl+Shift+F"),
        )
        self._find.setObjectName("searchAction")
        self._find.setText("查找")
        self._find.setEnabled(False)
        zoom_out = self._tool_action(
            "zoom-out", QStyle.SP_ArrowDown, "减小终端字体"
        )
        zoom_out.setObjectName("zoomOutAction")
        zoom_out.setEnabled(False)
        zoom_in = self._tool_action(
            "zoom-in", QStyle.SP_ArrowUp, "增大终端字体"
        )
        zoom_in.setObjectName("zoomInAction")
        zoom_in.setEnabled(False)
        self.addSeparator()
        interrupt = self._tool_action(
            "process-stop", QStyle.SP_MediaStop, "中断智能体（Ctrl+C）"
        )
        interrupt.setObjectName("interruptAction")
        interrupt.setEnabled(False)

        self._zoom_out_action = zoom_out
        self._zoom_in_action = zoom_in
        self._interrupt_action = interrupt

        self._copy.triggered.connect(lambda _checked=False: copy())
        self._paste.triggered.connect(lambda _checked=False: paste())
        self._find.triggered.connect(lambda _checked=False: find())
        zoom_out.triggered.connect(lambda _checked=False: zoom_out_callback())
        zoom_in.triggered.connect(lambda _checked=False: zoom_in_callback())
        interrupt.triggered.connect(lambda _checked=False: interrupt_callback())

    def _tool_action(self, icon_name, fallback, tooltip, shortcut=None):
        icon = QIcon.fromTheme(icon_name, self.style().standardIcon(fallback))
        action = self.addAction(icon, "")
        action.setToolTip(tooltip)
        action.setStatusTip(tooltip)
        if shortcut is not None:
            action.setShortcut(shortcut)
        return action

    def set_session_enabled(self, enabled, running):
        for action in (
            self._copy,
            self._find,
            self._zoom_out_action,
            self._zoom_in_action,
        ):
            action.setEnabled(enabled)
        self._interrupt_action.setEnabled(enabled and running)

    def set_paste_enabled(self, enabled):
        self._paste.setEnabled(enabled)

    def set_status(self, text: str, color: str) -> None:
        self._status.setText(f" {text} ")
        self._status.setStyleSheet(
            f"QLabel {{ color: {active().on_accent}; background: {color}; "
            "border-radius: 3px; "
            "padding: 2px 6px; }"
        )
