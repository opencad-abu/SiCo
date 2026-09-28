from __future__ import annotations

import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import sip
from PyQt5.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QStringListModel, Qt
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtWidgets import QApplication, QListView

from cadgui.wheel import WheelForwardingFilter, scroll_item_view


_APPLICATION = None


@pytest.fixture(scope="module")
def application() -> QApplication:
    global _APPLICATION
    _APPLICATION = QApplication.instance() or QApplication([])
    yield _APPLICATION


@pytest.fixture
def list_view(application: QApplication) -> QListView:
    view = QListView()
    view.setVerticalScrollMode(QListView.ScrollPerPixel)
    view.setModel(QStringListModel([f"Item {index:03d}" for index in range(200)]))
    view.resize(240, 140)
    view.show()
    application.processEvents()
    yield view
    view.close()
    application.processEvents()


def _wheel_event(
    view: QListView,
    *,
    pixel_delta: QPoint = QPoint(),
    angle_delta: QPoint = QPoint(),
    inside: bool = True,
) -> QWheelEvent:
    position = (
        view.viewport().rect().center()
        if inside
        else view.viewport().rect().bottomRight() + QPoint(100, 100)
    )
    return QWheelEvent(
        QPointF(position),
        QPointF(view.viewport().mapToGlobal(position)),
        pixel_delta,
        angle_delta,
        Qt.NoButton,
        Qt.NoModifier,
        Qt.ScrollUpdate,
        False,
    )


@pytest.mark.parametrize(
    ("pixel_delta", "angle_delta"),
    (
        (QPoint(0, -40), QPoint()),
        (QPoint(), QPoint(0, -15)),
        (QPoint(), QPoint(0, -120)),
    ),
)
def test_scroll_item_view_handles_pixel_and_angle_input(
    list_view: QListView,
    pixel_delta: QPoint,
    angle_delta: QPoint,
) -> None:
    event = _wheel_event(
        list_view,
        pixel_delta=pixel_delta,
        angle_delta=angle_delta,
    )
    scroll_bar = list_view.verticalScrollBar()

    assert scroll_bar.maximum() > 0
    assert scroll_item_view(list_view, event)
    assert event.isAccepted()
    assert scroll_bar.value() > 0


@pytest.mark.parametrize("target", ("viewport", "window"))
def test_wheel_filter_forwards_both_qt_delivery_paths_once(
    application: QApplication,
    list_view: QListView,
    target: str,
) -> None:
    event_filter = WheelForwardingFilter(list_view, list_view)
    event_filter.install()
    event = _wheel_event(list_view, angle_delta=QPoint(0, -120))
    watched = list_view.viewport() if target == "viewport" else list_view.windowHandle()
    assert watched is not None

    QApplication.sendEvent(watched, event)
    application.processEvents()

    expected = round(QApplication.wheelScrollLines() * list_view.sizeHintForRow(0))
    assert list_view.verticalScrollBar().value() == expected
    event_filter.remove()


def test_wheel_filter_ignores_events_outside_viewport(
    list_view: QListView,
) -> None:
    event_filter = WheelForwardingFilter(list_view, list_view)
    event = _wheel_event(
        list_view,
        angle_delta=QPoint(0, -120),
        inside=False,
    )

    assert event_filter.eventFilter(list_view.windowHandle(), event) is False
    assert list_view.verticalScrollBar().value() == 0


def test_wheel_filter_install_remove_and_reinstall_are_idempotent(
    application: QApplication,
    list_view: QListView,
) -> None:
    class TrackingWheelFilter(WheelForwardingFilter):
        def __init__(self, view: QListView) -> None:
            super().__init__(view, view)
            self.calls = 0

        def eventFilter(self, watched, event) -> bool:
            if event.type() == QEvent.Wheel:
                self.calls += 1
            return super().eventFilter(watched, event)

    event_filter = TrackingWheelFilter(list_view)
    watched = list_view.windowHandle()
    assert watched is not None

    event_filter.install()
    event_filter.install()
    first_event = _wheel_event(list_view, angle_delta=QPoint(0, -120))
    QApplication.sendEvent(watched, first_event)
    application.processEvents()
    first_value = list_view.verticalScrollBar().value()
    assert first_value > 0
    assert event_filter.calls == 1

    event_filter.remove()
    event_filter.remove()
    list_view.verticalScrollBar().setValue(0)
    removed_event = _wheel_event(list_view, angle_delta=QPoint(0, -120))
    QApplication.sendEvent(watched, removed_event)
    application.processEvents()
    assert event_filter.calls == 1

    event_filter.install()
    list_view.verticalScrollBar().setValue(0)
    reinstalled_event = _wheel_event(list_view, angle_delta=QPoint(0, -120))
    QApplication.sendEvent(watched, reinstalled_event)
    application.processEvents()
    assert list_view.verticalScrollBar().value() == first_value
    assert event_filter.calls == 2
    event_filter.remove()


def test_parented_wheel_filter_is_destroyed_with_its_view(
    application: QApplication,
) -> None:
    view = QListView()
    view.setModel(QStringListModel([f"Item {index:03d}" for index in range(20)]))
    event_filter = WheelForwardingFilter(view, view)
    event_filter.install()

    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application.processEvents()

    assert sip.isdeleted(view)
    assert sip.isdeleted(event_filter)
