"""Mouse-wheel forwarding for Qt item views launched from EDA sessions."""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QObject
from PyQt5.QtWidgets import QApplication, QAbstractItemView


def scroll_item_view(view: QAbstractItemView, event) -> bool:
    """Apply pixel or angle wheel input directly to an item-view scrollbar."""
    scroll_distance = event.pixelDelta().y()
    if not scroll_distance:
        angle_delta = event.angleDelta().y()
        if not angle_delta:
            return False
        row_height = view.sizeHintForRow(0)
        if row_height <= 0:
            row_height = view.fontMetrics().lineSpacing() + 6
        scroll_distance = round(
            angle_delta * max(1, QApplication.wheelScrollLines()) * row_height / 120
        )
        if not scroll_distance:
            scroll_distance = 1 if angle_delta > 0 else -1

    scroll_bar = view.verticalScrollBar()
    scroll_bar.setValue(scroll_bar.value() - scroll_distance)
    event.accept()
    return True


class WheelForwardingFilter(QObject):
    """Forward window/viewport wheel events that land inside an item view."""

    def __init__(self, view: QAbstractItemView, parent=None) -> None:
        super().__init__(parent)
        self.view = view
        self._application = None

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Wheel:
            viewport = self.view.viewport()
            window = self.view.window().windowHandle()
            if watched is viewport or watched is window:
                position = viewport.mapFromGlobal(event.globalPos())
                if viewport.rect().contains(position):
                    return scroll_item_view(self.view, event)
        return False

    def install(self) -> None:
        if self._application is not None:
            return
        application = QApplication.instance()
        if application is None:
            raise RuntimeError("QApplication must exist before wheel forwarding")
        application.installEventFilter(self)
        self._application = application

    def remove(self) -> None:
        if self._application is not None:
            self._application.removeEventFilter(self)
            self._application = None


def install_wheel_forwarding(view: QAbstractItemView) -> WheelForwardingFilter:
    """Install one application-level filter for viewport and window events."""
    event_filter = WheelForwardingFilter(view, view)
    event_filter.install()
    return event_filter


def remove_wheel_forwarding(event_filter: WheelForwardingFilter | None) -> None:
    """Remove a previously installed forwarding filter."""
    if event_filter is None:
        return
    event_filter.remove()
