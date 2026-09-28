"""Painting and input handling for progress and job action cells."""

from __future__ import annotations

import os
from pathlib import Path
from sicoresources import icon as resource_icon
from PyQt5.QtCore import QEvent, QModelIndex, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPalette
from PyQt5.QtWidgets import (
    QApplication, QStyle, QStyleOptionButton, QStyleOptionProgressBar,
    QStyledItemDelegate,
)
from .model_contracts import JOB_ID_ROLE, PROGRESS_ROLE
from .model_formatters import PROGRESS_NORMAL, PROGRESS_WARNING, PROGRESS_CRITICAL

def _close_icon_path() -> Path | None:
    return resource_icon("actions", "close-flow.png")


class ProgressCellDelegate(QStyledItemDelegate):
    """Render a compact progress bar while preserving the table row height."""

    def paint(self, painter, option, index: QModelIndex) -> None:
        progress = index.data(PROGRESS_ROLE)
        if progress is None:
            super().paint(painter, option, index)
            return
        ratio, text, *metadata = progress
        if ratio is None:
            super().paint(painter, option, index)
            return
        bar = QStyleOptionProgressBar()
        bar.rect = option.rect.adjusted(4, 4, -4, -4)
        bar.minimum = 0
        bar.maximum = 1000
        bar.progress = round(max(0.0, min(1.0, ratio)) * 1000)
        bar.text = text
        bar.textVisible = True
        bar.textAlignment = Qt.AlignCenter
        bar.palette = option.palette
        level = metadata[0] if metadata else PROGRESS_NORMAL
        colors = {
            PROGRESS_NORMAL: QColor("#3a7d70"),
            PROGRESS_WARNING: QColor("#d39b17"),
            PROGRESS_CRITICAL: QColor("#c9443c"),
        }
        bar.palette.setColor(QPalette.Highlight, colors[level])
        bar.palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        style = option.widget.style() if option.widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ProgressBar, bar, painter, option.widget)


class JobActionDelegate(QStyledItemDelegate):
    killRequested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._enabled = True
        self._icon_path = _close_icon_path()
        self._kill_icon = (
            QIcon(str(self._icon_path))
            if self._icon_path is not None
            else QIcon()
        )

    def set_enabled(self, enabled: bool) -> None:
        normalized = bool(enabled)
        self._enabled = normalized
        parent = self.parent()
        if parent is not None and hasattr(parent, "viewport"):
            parent.viewport().update()

    @staticmethod
    def _button_rect(option):
        return option.rect.adjusted(6, 3, -6, -3)

    def paint(self, painter, option, index: QModelIndex) -> None:
        style = option.widget.style() if option.widget is not None else QApplication.style()
        icon = self._kill_icon
        if icon.isNull():
            icon = style.standardIcon(
                QStyle.SP_DialogCloseButton, None, option.widget
            )
        button = QStyleOptionButton()
        button.rect = self._button_rect(option)
        button.icon = icon
        button.iconSize = QSize(18, 18)
        button.state = QStyle.State_Raised
        if self._enabled:
            button.state |= QStyle.State_Enabled
        button.palette = option.palette
        style.drawControl(QStyle.CE_PushButton, button, painter, option.widget)

    def editorEvent(self, event, model, option, index: QModelIndex) -> bool:
        if (
            not self._enabled
            or event.type() != QEvent.MouseButtonRelease
            or event.button() != Qt.LeftButton
            or not self._button_rect(option).contains(event.pos())
        ):
            return False
        job_id = str(index.data(JOB_ID_ROLE) or "")
        if not job_id:
            return False
        self.killRequested.emit(job_id)
        return True
