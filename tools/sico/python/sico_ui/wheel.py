"""EDA window/viewport wheel routing, adapted from common/cadgui/wheel.py."""

from __future__ import annotations

import math
import os

from PyQt5.QtCore import QEvent, QObject, Qt
from PyQt5.QtWidgets import QApplication, QPlainTextEdit


def prepare_wheel_environment():
    """Clear inherited EDA XI2 switches before creating a QApplication."""
    for name in ("QT_XCB_NO_XI2", "QT_XCB_NO_XI2_MOUSE"):
        os.environ.pop(name, None)


class WheelRouter(QObject):
    """Route wheel input once to the visible scroll area under the pointer."""

    def __init__(self, window, views):
        super().__init__(window)
        self.window, self.views = window, views
        self.application = None
        self.remainder = {}

    def install(self):
        if self.application is None:
            self.application = QApplication.instance()
            self.application.installEventFilter(self)

    def remove(self):
        if self.application is not None:
            self.application.removeEventFilter(self)
            self.application = None
        self.remainder.clear()

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Wheel or event.modifiers() != Qt.NoModifier:
            return False
        if QApplication.activeModalWidget() is not None:
            return False
        for view in self.views:
            viewport = view.viewport()
            if not view.isVisible() or watched not in (
                viewport,
                self.window.windowHandle(),
                view.window().windowHandle(),
            ):
                continue
            if not viewport.rect().contains(viewport.mapFromGlobal(event.globalPos())):
                continue
            bar = view.verticalScrollBar()
            if event.pixelDelta().y():
                distance = event.pixelDelta().y()
                if isinstance(view, QPlainTextEdit):
                    distance /= max(1, view.fontMetrics().lineSpacing())
            elif event.angleDelta().y():
                distance = (
                    event.angleDelta().y()
                    / 120
                    * max(1, QApplication.wheelScrollLines())
                    * max(1, bar.singleStep())
                )
            else:
                return False
            previous = self.remainder.get(view, 0)
            accumulated = distance + (previous if distance * previous >= 0 else 0)
            steps = math.trunc(accumulated)
            self.remainder[view] = accumulated - steps
            bar.setValue(bar.value() - steps)
            event.accept()
            return True
        return False
