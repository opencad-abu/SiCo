"""Prepare the DRC Qt import boundary and report runtime setup errors."""

import sys
from cadgui.environment import prepare_qt_environment

# Clean Virtuoso plugin paths before any selector module imports Qt.
prepare_qt_environment()

try:
    from PyQt5.QtCore import (
        QModelIndex, QSignalBlocker, QSortFilterProxyModel, QTimer, Qt, pyqtSignal,
    )
    from PyQt5.QtGui import (
        QColor, QCloseEvent, QFont, QIcon, QPalette, QStandardItem,
        QStandardItemModel, QWheelEvent,
    )
    from PyQt5.QtWidgets import (
        QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
        QLabel, QLineEdit, QMessageBox, QPushButton, QStyle, QTreeView, QVBoxLayout,
    )
    from cadgui.chrome import SiDialog
    from cadgui.prompts import notice
    from cadgui.wheel import WheelForwardingFilter, scroll_item_view
except ImportError as exc:  # pragma: no cover - production setup error
    raise RuntimeError(
        f"PyQt5 is unavailable in {sys.executable}. DRC Rule Select requires "
        "the configured Python 3.9 runtime with PyQt5 installed."
    ) from exc

__all__ = [
    "QModelIndex", "QSignalBlocker", "QSortFilterProxyModel", "QTimer", "Qt", "pyqtSignal",
    "QColor", "QCloseEvent", "QFont", "QIcon", "QPalette", "QStandardItem",
    "QStandardItemModel", "QWheelEvent", "QApplication", "QDialog", "QDialogButtonBox",
    "QHBoxLayout", "QHeaderView", "QLabel", "QLineEdit", "QMessageBox", "QPushButton",
    "QStyle", "QTreeView", "QVBoxLayout", "WheelForwardingFilter", "scroll_item_view",
    "SiDialog", "notice",
]
