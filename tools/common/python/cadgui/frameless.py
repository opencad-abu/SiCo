"""Frameless window behaviour and flag policy shared by every family window.

Each window owns its single drag/resize state; edge grips send interaction events
through that window API, so no state lives on a second host.
"""

from __future__ import annotations

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import QApplication, QWidget


def half_screen_rect(side="right", anchor=None):
    """Half of the working screen: SiCo decides where its windows go.

    A SiCo window as the anchor picks the screen when it has one; otherwise the
    screen under the pointer decides, so a window lands where the user is
    working instead of on a random monitor.
    """
    screen = None
    if isinstance(anchor, QWidget):
        screen = anchor.screen()
    if screen is None:
        screen = QApplication.screenAt(QCursor.pos())
    if screen is None:
        screen = QApplication.primaryScreen()
    if screen is None:
        return None
    area = screen.availableGeometry()
    width = max(1, area.width() // 2)
    left = area.left() if side == "left" else area.right() + 1 - width
    return QRect(left, area.top(), width, area.height())


def main_window_frameless_flags():
    """Return the frameless flag set for custom-title main windows."""
    return (
        Qt.FramelessWindowHint
        | Qt.WindowMinMaxButtonsHint
        | Qt.WindowSystemMenuHint
    )


def apply_main_window_frameless(window):
    """Apply the custom-border flags to a main window."""
    window.setWindowFlags(main_window_frameless_flags())


def dialog_frameless_flags():
    """Return the frameless flag set for top-level dialogs."""
    return (
        Qt.Window
        | Qt.FramelessWindowHint
        | Qt.WindowSystemMenuHint
    )


def apply_dialog_frameless(dialog):
    """Apply the custom-border flags to a dialog-like window."""
    dialog.setWindowFlags(dialog_frameless_flags())


class EdgeGrip(QWidget):
    """Thin border strip; dragging it resizes the window in one direction."""

    def __init__(self, window, direction):
        super().__init__(window)
        self.setObjectName("copilotEdgeGrip")
        self.host_window = window
        self.direction = direction
        self.setCursor(FramelessWindowMixin.edge_cursor(direction))
        self.setMouseTracking(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.host_window.begin_resize(event.globalPos(), self.direction)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self.host_window.continue_resize(event.globalPos())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.host_window.end_drag_resize()
        super().mouseReleaseEvent(event)


class FramelessWindowMixin:
    """Frameless behaviour: title-bar drag, edge resize, double-click maximize.

    The border is grabbed through the window's own edge grips instead of an
    event filter, so the chrome never walks foreign widget trees.
    """

    edge_margin = 6

    def init_frameless(self, title_bar):
        self._frameless_title_bar = title_bar
        self._frameless_drag = None
        self._frameless_resize = None
        self._frameless_grips = []
        self.setMouseTracking(True)
        self._install_edge_grips()

    # -- drag and resize API (used by the title bar and the edge grips) ----

    def begin_drag(self, global_position):
        # 交给窗口管理器搬运，桌面才能照常贴边（半屏/最大化）；自绘标题栏自己
        # move() 的话，WM 看不到这次拖动，就不会触发贴边效果。
        handle = self.system_move_handle()
        if handle is not None and handle.startSystemMove():
            self._frameless_drag = None
            return
        self._frameless_drag = global_position - self.frameGeometry().topLeft()

    def system_move_handle(self):
        """The window handle able to run a window-manager move, if any."""
        return self.windowHandle()

    def continue_drag(self, global_position):
        if self._frameless_drag is not None:
            self.move(global_position - self._frameless_drag)

    def begin_resize(self, global_position, direction):
        if self.isMaximized():
            return
        self._frameless_resize = (global_position, self.geometry(), direction)

    def continue_resize(self, global_position):
        if self._frameless_resize is None:
            return
        origin, geometry, direction = self._frameless_resize
        direction = direction.lower()  # Corner names use topLeft/bottomRight casing.
        delta = global_position - origin
        rect = QRect(geometry)
        if "left" in direction:
            rect.setLeft(rect.left() + delta.x())
        if "right" in direction:
            rect.setRight(rect.right() + delta.x())
        if "top" in direction:
            rect.setTop(rect.top() + delta.y())
        if "bottom" in direction:
            rect.setBottom(rect.bottom() + delta.y())
        minimum = self.minimumSize()
        if rect.width() < minimum.width():
            if "left" in direction:
                rect.setLeft(rect.right() - minimum.width() + 1)
            else:
                rect.setRight(rect.left() + minimum.width() - 1)
        if rect.height() < minimum.height():
            if "top" in direction:
                rect.setTop(rect.bottom() - minimum.height() + 1)
            else:
                rect.setBottom(rect.top() + minimum.height() - 1)
        self.setGeometry(rect)

    def end_drag_resize(self):
        self._frameless_drag = None
        self._frameless_resize = None
        self.unsetCursor()

    def toggle_maximized(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # -- geometry helpers -------------------------------------------------

    @staticmethod
    def edge_cursor(direction):
        cursors = {
            "left": Qt.SplitHCursor,
            "right": Qt.SplitHCursor,
            "top": Qt.SplitVCursor,
            "bottom": Qt.SplitVCursor,
            "topLeft": Qt.SizeFDiagCursor,
            "topRight": Qt.SizeBDiagCursor,
            "bottomLeft": Qt.SizeBDiagCursor,
            "bottomRight": Qt.SizeFDiagCursor,
        }
        return cursors.get(direction, Qt.ArrowCursor)

    def _title_bar_area(self, position):
        bar = getattr(self, "_frameless_title_bar", None)
        return bar is not None and bar.isVisible() and bar.geometry().contains(position)

    def _edge_direction(self, position):
        if self.isMaximized() or not self.rect().contains(position):
            return None
        margin = self.edge_margin
        x, y = position.x(), position.y()
        top = y <= margin
        bottom = y >= self.height() - margin
        left = x <= margin
        right = x >= self.width() - margin
        if top and left:
            return "topLeft"
        if top and right:
            return "topRight"
        if bottom and left:
            return "bottomLeft"
        if bottom and right:
            return "bottomRight"
        if left:
            return "left"
        if right:
            return "right"
        if top:
            return "top"
        if bottom:
            return "bottom"
        return None

    # -- edge grips -------------------------------------------------------

    def _install_edge_grips(self):
        for direction in (
            "top", "bottom", "left", "right",
            "topLeft", "topRight", "bottomLeft", "bottomRight",
        ):
            self._frameless_grips.append(EdgeGrip(self, direction))
        self._sync_edge_grips()

    def _sync_edge_grips(self):
        margin = self.edge_margin
        width, height = self.width(), self.height()
        boxes = {
            "top": (0, 0, width, margin),
            "bottom": (0, height - margin, width, margin),
            "left": (0, margin, margin, max(0, height - 2 * margin)),
            "right": (width - margin, margin, margin, max(0, height - 2 * margin)),
            "topLeft": (0, 0, margin, margin),
            "topRight": (width - margin, 0, margin, margin),
            "bottomLeft": (0, height - margin, margin, margin),
            "bottomRight": (width - margin, height - margin, margin, margin),
        }
        for grip in self._frameless_grips:
            grip.setGeometry(*boxes[grip.direction])
            grip.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_edge_grips()

    def showEvent(self, event):
        self._sync_edge_grips()
        super().showEvent(event)

    # -- window level fallbacks -------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            global_position = event.globalPos()
            local = self.mapFromGlobal(global_position)
            if self._title_bar_area(local):
                self.begin_drag(global_position)
                event.accept()
                return
            direction = self._edge_direction(local)
            if direction:
                self.begin_resize(global_position, direction)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._frameless_resize is not None:
            self.continue_resize(event.globalPos())
            event.accept()
            return
        if self._frameless_drag is not None:
            self.continue_drag(event.globalPos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.end_drag_resize()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self._title_bar_area(
            self.mapFromGlobal(event.globalPos())
        ):
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
