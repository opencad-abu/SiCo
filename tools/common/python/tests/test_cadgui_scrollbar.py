"""Shared theme scrollbar geometry: the handle must not cover end arrows."""

from __future__ import annotations

import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QRect, QSize, Qt
from PyQt5.QtWidgets import (
    QApplication,
    QScrollBar,
    QStyle,
    QStyleFactory,
    QStyleOptionSlider,
)

from cadgui.theme import COPILOT_STYLESHEET


_APPLICATION = None


@pytest.fixture(scope="module")
def application() -> QApplication:
    global _APPLICATION
    _APPLICATION = QApplication.instance() or QApplication([])
    yield _APPLICATION


def _scroll_bar(
    application: QApplication, orientation, size: QSize, style_name: str
) -> QScrollBar:
    bar = QScrollBar(orientation)
    bar.setRange(0, 100)
    bar.setPageStep(10)
    bar.resize(size)
    bar.setStyleSheet(COPILOT_STYLESHEET)
    bar._native_style = QStyleFactory.create(style_name)
    bar.setStyle(bar._native_style)
    bar.show()
    application.processEvents()
    return bar


def _sub_control_rect(bar: QScrollBar, sub_control) -> QRect:
    option = QStyleOptionSlider()
    bar.initStyleOption(option)
    return bar.style().subControlRect(
        QStyle.CC_ScrollBar, option, sub_control, bar
    )


@pytest.mark.parametrize(
    ("orientation", "size"),
    (
        (Qt.Vertical, QSize(14, 240)),
        (Qt.Horizontal, QSize(240, 14)),
    ),
)
@pytest.mark.parametrize("style_name", QStyleFactory.keys())
@pytest.mark.parametrize("at_maximum", (False, True))
def test_scroll_bar_handle_stays_between_the_end_arrows(
    application: QApplication,
    orientation,
    size: QSize,
    style_name: str,
    at_maximum: bool,
) -> None:
    bar = _scroll_bar(application, orientation, size, style_name)
    try:
        bar.setValue(bar.maximum() if at_maximum else bar.minimum())
        application.processEvents()
        sub_line = _sub_control_rect(bar, QStyle.SC_ScrollBarSubLine)
        add_line = _sub_control_rect(bar, QStyle.SC_ScrollBarAddLine)
        slider = _sub_control_rect(bar, QStyle.SC_ScrollBarSlider)
        if orientation == Qt.Vertical:
            assert slider.top() >= sub_line.bottom()
            assert slider.bottom() <= add_line.top()
        else:
            assert slider.left() >= sub_line.right()
            assert slider.right() <= add_line.left()
    finally:
        bar.close()
        application.processEvents()
