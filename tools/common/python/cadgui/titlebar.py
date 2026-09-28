"""Family title bar: the draggable strip that carries the window controls."""

from __future__ import annotations

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .glyphs import TITLE_GLYPH_SIZE, close_icon, maximize_icon, minimize_icon

# 标题栏三个按钮：Qt 自绘字形，关闭保留红色，其余用强调色。
WINDOW_BUTTONS = (
    ("minimize", minimize_icon, "最小化"),
    ("maximize", maximize_icon, "最大化"),
    ("close", close_icon, "关闭窗口"),
)


WINDOW_CONTROLS = tuple(key for key, _, _ in WINDOW_BUTTONS)


class SiTitleBar(QWidget):
    """Draggable title bar hosting the minimize/maximize/close controls."""

    def __init__(self, window, title, controls=WINDOW_CONTROLS, mark=None):
        super().__init__(window)
        self.setObjectName("copilotTitleBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(32)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        content = QWidget(self)
        content.setObjectName("copilotTitleContent")
        column.addWidget(content, 1)
        layout = QHBoxLayout(content)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(6)
        # 品牌图标由调用方给：SiCo 用产品 logo，独立流程用站点 logo 或不放。
        self.mark = mark
        if mark is not None and not mark.isNull():
            logo = QLabel()
            logo.setObjectName("copilotTitleLogo")
            logo.setFixedWidth(26)
            logo.setAlignment(Qt.AlignCenter)
            logo.setPixmap(mark.pixmap(20, 20))
            layout.addWidget(logo)
        self.title = QLabel(title)
        self.title.setObjectName("copilotTitleText")
        self.title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.title.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        layout.addWidget(self.title, 1)
        self.buttons = {}
        for key, glyph, tooltip in WINDOW_BUTTONS:
            if key not in controls:
                continue
            button = QToolButton()
            button.setObjectName("copilotTitleClose" if key == "close" else "copilotTitleButton")
            button.setIcon(glyph(TITLE_GLYPH_SIZE))
            button.setIconSize(QSize(TITLE_GLYPH_SIZE, TITLE_GLYPH_SIZE))
            button.setFixedSize(QSize(26, 24))
            button.setAutoRaise(True)
            button.setToolTip(tooltip)
            button.setCursor(Qt.ArrowCursor)
            self.buttons[key] = button
            layout.addWidget(button)
        self.host_window = window
        self.rule = QWidget(self)
        self.rule.setObjectName("copilotTitleRule")
        self.rule.setAttribute(Qt.WA_StyledBackground, True)
        self.rule.setFixedHeight(3)
        column.addWidget(self.rule)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.host_window.begin_drag(event.globalPos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self.host_window.continue_drag(event.globalPos())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.host_window.end_drag_resize()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.host_window.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
