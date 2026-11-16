"""Window interaction boundaries exercised with real Qt widgets offscreen."""

from __future__ import annotations

import pytest
from cadgui import chrome as chrome_impl
from cadgui import frameless as frameless_impl
from PyQt5.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PyQt5.QtGui import QMouseEvent, QResizeEvent, QShowEvent
from PyQt5.QtWidgets import QWidget
from sico_ui import chrome


@pytest.mark.parametrize(
    "direction,delta,expected",
    [
        ("left", QPoint(-20, 0), QRect(80, 100, 320, 240)),
        ("right", QPoint(20, 0), QRect(100, 100, 320, 240)),
        ("top", QPoint(0, -20), QRect(100, 80, 300, 260)),
        ("bottom", QPoint(0, 20), QRect(100, 100, 300, 260)),
        ("topLeft", QPoint(-20, -20), QRect(80, 80, 320, 260)),
        ("topRight", QPoint(20, -20), QRect(100, 80, 320, 260)),
        ("bottomLeft", QPoint(-20, 20), QRect(80, 100, 320, 260)),
        ("bottomRight", QPoint(20, 20), QRect(100, 100, 320, 260)),
    ],
)
def test_resize_moves_each_selected_edge(direction, delta, expected):
    window = chrome.SiWidget()
    window.setGeometry(100, 100, 300, 240)
    window.begin_resize(QPoint(400, 400), direction)
    original = window._frameless_resize[1]
    window.continue_resize(QPoint(400, 400) + delta)
    assert window.geometry() == expected
    assert original == QRect(100, 100, 300, 240)
    window.end_drag_resize()
    assert window._frameless_resize is None and window._frameless_drag is None


@pytest.mark.parametrize(
    "direction,delta,expected",
    [
        ("left", QPoint(500, 0), QRect(220, 100, 180, 240)),
        ("right", QPoint(-500, 0), QRect(100, 100, 180, 240)),
        ("top", QPoint(0, 500), QRect(100, 220, 300, 120)),
        ("bottom", QPoint(0, -500), QRect(100, 100, 300, 120)),
        ("topLeft", QPoint(500, 500), QRect(220, 220, 180, 120)),
        ("bottomRight", QPoint(-500, -500), QRect(100, 100, 180, 120)),
    ],
)
def test_resize_minimum_keeps_opposite_edges_fixed(direction, delta, expected):
    window = chrome.SiWidget()
    window.setMinimumSize(180, 120)
    window.setGeometry(100, 100, 300, 240)
    window.begin_resize(QPoint(400, 400), direction)
    window.continue_resize(QPoint(400, 400) + delta)
    assert window.geometry() == expected


@pytest.mark.parametrize("window_class", (chrome.SiMainWindow, chrome.SiWidget,
                                          chrome.SiDialog))
def test_window_edges_keep_a_two_pixel_horizontal_inset(window_class):
    """组件边框不贴窗口边缘：左右各留 2 像素，边缘框线不再被裁掉。"""
    window = window_class("边缘留白")
    try:
        menu_bar = getattr(window, "menu_bar", None)
        if menu_bar is not None:
            menu_bar.hide()
        if window_class is chrome.SiMainWindow:
            body = QWidget()
            window.setCentralWidget(body)
        else:
            body = window.body
        window.resize(360, 240)
        window.show()
        assert window.title_bar.geometry().x() == 2
        geometry = body.geometry()
        assert geometry.x() == 2
        assert geometry.width() == window.width() - 4
    finally:
        window.close()


def test_drag_state_is_per_window_and_maximized_resize_is_ignored(monkeypatch):
    left, right = chrome.SiWidget(), chrome.SiWidget()
    monkeypatch.setattr(left, "system_move_handle", lambda: None)
    left.begin_drag(left.frameGeometry().topLeft() + QPoint(20, 30))
    assert left._frameless_drag == QPoint(20, 30)
    assert right._frameless_drag is None
    assert left._frameless_grips is not right._frameless_grips
    assert all(grip.parentWidget() is left for grip in left._frameless_grips)
    left.showMaximized()
    left.begin_resize(QPoint(100, 100), "right")
    assert left._frameless_resize is None
    left.end_drag_resize()
    assert left._frameless_drag is None


def test_window_composition_resolves_screen_and_button_overrides_at_call_time(monkeypatch):
    half = QRect(50, 60, 400, 500)
    calls = []
    window = chrome.SiWidget()
    window.launch_half = "right"

    def screen(side, anchor=None):
        calls.append((side, anchor))
        return half

    monkeypatch.setattr(chrome_impl, "half_screen_rect", screen)
    assert window.place_on_launch_half(window) == half
    assert calls == [("right", window)]
    assert window.geometry() == half
    monkeypatch.setattr(window, "toggle_maximized", lambda: calls.append("maximize"))
    monkeypatch.setattr(window, "close_from_chrome", lambda: calls.append("close"))
    window.title_bar.buttons["maximize"].click()
    window.title_bar.buttons["close"].click()
    assert calls[-2:] == ["maximize", "close"]


def test_policy_screen_priority_and_no_screen_fallback(monkeypatch):
    class Screen:
        def __init__(self, rect):
            self.rect = rect

        def availableGeometry(self):
            return self.rect

    anchor = QWidget()
    primary = Screen(QRect(0, 0, 1000, 700))
    pointed = Screen(QRect(1000, 0, 801, 600))
    anchored = Screen(QRect(-900, 20, 901, 800))
    monkeypatch.setattr(frameless_impl.QApplication, "screenAt", lambda point: pointed)
    monkeypatch.setattr(frameless_impl.QApplication, "primaryScreen", lambda: primary)
    monkeypatch.setattr(anchor, "screen", lambda: anchored)
    assert chrome.half_screen_rect("left", anchor) == QRect(-900, 20, 450, 800)
    assert chrome.half_screen_rect("right") == QRect(1401, 0, 400, 600)
    monkeypatch.setattr(frameless_impl.QApplication, "screenAt", lambda point: None)
    assert chrome.half_screen_rect("left") == QRect(0, 0, 500, 700)
    monkeypatch.setattr(frameless_impl.QApplication, "primaryScreen", lambda: None)
    assert chrome.half_screen_rect() is None


def test_window_centers_on_a_screen_point_and_stays_visible(monkeypatch):
    class Screen:
        def availableGeometry(self):
            return QRect(100, 50, 800, 600)

    monkeypatch.setattr(chrome_impl.QApplication, "screenAt", lambda point: Screen())
    window = chrome.SiWidget()
    window.resize(200, 100)
    try:
        assert window.center_on_point(QPoint(500, 350)) == QPoint(400, 300)
        assert window.center_on_point(QPoint(110, 60)) == QPoint(100, 50)
        assert window.center_on_point(QPoint(890, 640)) == QPoint(700, 550)
    finally:
        window.close()


def test_qt_super_event_order_and_first_show_centering():
    calls = []

    class NativeTail(QWidget):
        def showEvent(self, event):
            calls.append("native-show")
            super().showEvent(event)

        def resizeEvent(self, event):
            calls.append("native-resize")
            super().resizeEvent(event)

        def mousePressEvent(self, event):
            calls.append("native-press")
            super().mousePressEvent(event)

        def mouseReleaseEvent(self, event):
            calls.append("native-release")
            super().mouseReleaseEvent(event)

    class Probe(chrome.SiWindowMixin, NativeTail):
        launch_center = True

        def _sync_edge_grips(self):
            calls.append("grips")
            super()._sync_edge_grips()

        def center_on_anchor(self, anchor=None):
            calls.append("center")

        def end_drag_resize(self):
            calls.append("end")
            super().end_drag_resize()

    window = Probe()
    window.install_chrome()
    calls.clear()
    window.showEvent(QShowEvent())
    assert calls == ["grips", "native-show", "center"]
    calls.clear()
    window.showEvent(QShowEvent())
    assert calls == ["grips", "native-show"]
    calls.clear()
    window.resizeEvent(QResizeEvent(QSize(300, 200), QSize(200, 100)))
    assert calls == ["native-resize", "grips"]
    calls.clear()
    press = QMouseEvent(QEvent.MouseButtonPress, QPoint(20, 20), Qt.RightButton,
                        Qt.RightButton, Qt.NoModifier)
    release = QMouseEvent(QEvent.MouseButtonRelease, QPoint(20, 20), Qt.RightButton,
                          Qt.NoButton, Qt.NoModifier)
    window.mousePressEvent(press)
    window.mouseReleaseEvent(release)
    assert calls == ["native-press", "end", "native-release"]
