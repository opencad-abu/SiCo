"""Equal-width tab widgets and shared option rows."""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QSizePolicy, QStackedWidget, QTabBar, QTabWidget, QVBoxLayout, QWidget


class EqualTabBar(QTabBar):
    """Tab bar whose tabs always divide the available width evenly."""

    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        count = max(1, self.count())
        available = self.width()
        if available > 0:
            size.setWidth(max(1, available // count))
        return size

    def sizeHint(self):
        """Keep the height, but never let the labels push the window wider."""
        hint = super().sizeHint()
        hint.setWidth(0)
        return hint

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

class EvenTabWidget(QTabWidget):
    """Tab widget whose tab bar spans the whole width and splits it evenly."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabBar(EqualTabBar())
        self.setDocumentMode(True)
        self.setUsesScrollButtons(False)
        self.tabBar().setExpanding(True)
        self.tabBar().setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.tabBar().setStyleSheet("QTabBar::tab { min-width: 0px; }")

class EvenTabPanel(QWidget):
    """Tab bar on top, one shared option row under it, then the stacked pages.

    右侧"中心"的筛选行要跟着标签栏走：标签栏固定在容器顶部（和左侧导航、
    中央页签在同一水平线上），选项行放在标签下面、页面之上；页面占满剩余
    空间。API 覆盖窗口和测试用到的 QTabWidget 子集。
    """

    currentChanged = pyqtSignal(int)

    def __init__(self, options=(), parent=None):
        super().__init__(parent)
        self.bar = EqualTabBar()
        self.bar.setExpanding(True)
        self.bar.setUsesScrollButtons(False)
        self.bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.bar.setStyleSheet("QTabBar::tab { min-width: 0px; }")
        self.stack = QStackedWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.bar)
        # 筛选行与标签栏、页面之间留出层间隙，避免挤成一排。
        for index, widget in enumerate(options):
            if index:
                layout.addSpacing(3)
            layout.addWidget(widget)
        if options:
            layout.addSpacing(8)
        layout.addWidget(self.stack, 1)
        self.bar.currentChanged.connect(self._bar_changed)

    def _bar_changed(self, index):
        self.stack.setCurrentIndex(index)
        self.currentChanged.emit(index)

    def addTab(self, widget, label):
        index = self.stack.addWidget(widget)
        self.bar.addTab(label)
        return index

    def count(self):
        return self.bar.count()

    def tabText(self, index):
        return self.bar.tabText(index)

    def setTabText(self, index, text):
        self.bar.setTabText(index, text)

    def widget(self, index):
        return self.stack.widget(index)

    def indexOf(self, widget):
        return self.stack.indexOf(widget)

    def currentIndex(self):
        return self.bar.currentIndex()

    def setCurrentIndex(self, index):
        self.bar.setCurrentIndex(index)

    def currentWidget(self):
        return self.stack.currentWidget()

    def setCurrentWidget(self, widget):
        self.setCurrentIndex(self.indexOf(widget))

    def tabBar(self):
        return self.bar
