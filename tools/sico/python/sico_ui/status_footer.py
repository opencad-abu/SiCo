"""Fit the compact session status bar rows."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QFontMetrics
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

STATUS_POINT_SIZE = 6


def install_status_bar(window):
    """Build the platform/session, service and model footer labels."""
    initial_font = QFont(window.font())
    initial_font.setPointSize(STATUS_POINT_SIZE)
    status_height = QFontMetrics(initial_font).height() + 4
    window.status = QLabel()
    window.status.setObjectName("sessionStatus")
    window.status.setTextFormat(Qt.PlainText)
    window.status.setWordWrap(False)
    window.status.setMinimumWidth(0)
    window.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    window.status.setFixedHeight(status_height)
    window.status_box = QWidget()
    window.status_box.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    window._status_layout = QVBoxLayout(window.status_box)
    window._status_layout.setContentsMargins(0, 0, 0, 0)
    window._status_layout.setSpacing(0)
    window._status_layout.addWidget(window.status)
    window.statusBar().addWidget(window.status_box, 1)
    window.service_status = QWidget()
    window.service_status.setObjectName("serviceStatus")
    window.service_status.setFixedHeight(status_height)
    window.service_status.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    window.service_status.hide()
    window.service_status_layout = QHBoxLayout(window.service_status)
    window.service_status_layout.setContentsMargins(0, 0, 0, 0)
    window.service_status_layout.setSpacing(8)
    window.statusBar().addWidget(window.service_status)
    window.statusBar().addWidget(QWidget(), 1)
    window.model_status = QLabel()
    window.model_status.setObjectName("modelStatus")
    window.model_status.setTextFormat(Qt.PlainText)
    window.model_status.setMaximumWidth(240)
    window.model_status.setWordWrap(False)
    window.model_status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    window.model_status.setFixedHeight(status_height)
    window.statusBar().addPermanentWidget(window.model_status)


def base_point_size(application, fallback_font):
    """Interface size used as the reference for the compact footer."""
    point = application.font().pointSize() if application is not None else 0
    if point <= 0:
        point = fallback_font.pointSize()
    return point if point > 0 else 10


def status_point_size():
    """Bottom status rows use a fixed compact size, independent of user scaling."""
    return STATUS_POINT_SIZE


def status_font(reference_font):
    """One compact footer font shared by every bottom status label."""
    font = QFont(reference_font)
    font.setPointSize(STATUS_POINT_SIZE)
    return font


def fit_status_rows(layout, container, font, status_bar=None):
    """Lay out the one-line platform/session rows from their current font."""
    heights = []
    for index in range(layout.count()):
        label = layout.itemAt(index).widget()
        label.setFont(font)
        label.setWordWrap(False)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        height = label.fontMetrics().height() + 4
        label.setFixedHeight(height)
        heights.append(height)
    container.setFixedHeight(sum(heights))
    if status_bar is not None:
        status_bar.updateGeometry()


def fit_service_status(container, font, default_height=None):
    """Size the middle service labels and their one-line container."""
    labels = container.findChildren(QLabel)
    for label in labels:
        label.setFont(font)
    heights = [label.fontMetrics().height() + 4 for label in labels]
    for label, height in zip(labels, heights):
        label.setFixedHeight(height)
    if default_height is None:
        default_height = QFontMetrics(font).height() + 4
    container.setFixedHeight(max(heights, default=default_height))
