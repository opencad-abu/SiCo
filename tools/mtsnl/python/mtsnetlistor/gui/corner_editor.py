"""Per-cell corner profile editor using explicit ordered model bundles."""
from __future__ import annotations

from pathlib import Path

from cadgui.chrome import SiDialog
from cadgui.prompts import ask_files, ask_text, notice
from PyQt5.QtCore import pyqtSignal, Qt
from PyQt5.QtWidgets import (QDialog, QWidget, QHBoxLayout, QVBoxLayout, QComboBox,
                            QPushButton, QLineEdit, QDialogButtonBox, QTableWidget,
                            QTableWidgetItem, QLabel)

from ..model import CornerExport, CornerProfile, ModelEntry
from ..model_corners import model_corners


class CornerEditor(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._host = parent
        self._profiles = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.mode = QComboBox(self)
        self.mode.addItem("Fixed corner", "fixed")
        self.mode.addItem("Spectre corner library", "library")
        self.variable = QLineEdit("mts_corner", self)
        self.variable.setPlaceholderText("ADE global string variable")
        edit = QPushButton("Edit corner profiles…", self)
        edit.clicked.connect(self.edit_profiles)
        row.addWidget(self.mode)
        row.addWidget(self.variable)
        row.addWidget(edit)
        layout.addLayout(row)
        self.hint = QLabel(self)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.mode.currentIndexChanged.connect(self._changed)
        self.variable.textChanged.connect(self._changed)
        self._changed()

    def _changed(self):
        library = self.mode.currentData() == "library"
        self.variable.setEnabled(library)
        self.hint.setText((f'Section: VAR("{self.variable.text()}") • ' +
                           ", ".join(p.name for p in self._profiles) +
                           ' • Each profile owns its complete model list.') if library else "")
        self.changed.emit()

    def value(self):
        return CornerExport(self.mode.currentData(), self.variable.text(), self._profiles)

    def set_value(self, value):
        self._profiles = value.profiles
        self.variable.setText(value.variable)
        self.mode.setCurrentIndex(self.mode.findData(value.mode))
        self._changed()

    def edit_profiles(self):
        # Reuse the fixed list only as the first explicit profile. Duplicating a
        # profile copies all rows; each section remains the user's explicit edit.
        seed = self._profiles
        if not seed and hasattr(self._host, "_model_rows"):
            seed = (CornerProfile("tt", tuple(ModelEntry(Path(f), s, label, e)
                    for e, f, s, label in self._host._model_rows() if f.strip())),)
        dialog = ProfileDialog(seed, self)
        if dialog.exec_() == QDialog.Accepted:
            self._profiles = dialog.profiles()
            self._changed()


class ProfileDialog(SiDialog):
    def __init__(self, profiles, parent=None):
        super().__init__("Corner profiles — complete ordered model bundles", parent, ("close",))
        self.resize(850, 420)
        self._profiles = list(profiles)
        self._current = -1
        layout = QVBoxLayout()
        self.content_layout().addLayout(layout)
        top = QHBoxLayout()
        self.selector = QComboBox(self)
        top.addWidget(self.selector)
        for label, action in (("Add", self.add_profile), ("Duplicate", lambda: self.add_profile(True)),
                              ("Rename", self.rename_profile), ("Remove", self.remove_profile)):
            button = QPushButton(label, self)
            button.clicked.connect(action)
            top.addWidget(button)
        layout.addLayout(top)
        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["Enabled", "Model File", "Section", "Label"])
        self.table.setColumnWidth(1, 450)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        for label, action in (("Add model…", self.add_model), ("Remove model", self.remove_model),
                              ("Move up", lambda: self.move_model(-1)), ("Move down", lambda: self.move_model(1))):
            button = QPushButton(label, self)
            button.clicked.connect(action)
            actions.addWidget(button)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.selector.currentIndexChanged.connect(self.select)
        self.refresh()

    def save(self):
        if self._current < 0:
            return
        models = []
        for row in range(self.table.rowCount()):
            models.append(ModelEntry(Path(self.table.item(row, 1).text()),
                         self.table.cellWidget(row, 2).currentText(), self.table.item(row, 3).text(),
                         self.table.item(row, 0).checkState() == Qt.Checked))
        self._profiles[self._current] = CornerProfile(self._profiles[self._current].name, tuple(models))

    def refresh(self, index=0):
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItems([p.name for p in self._profiles])
        self.selector.setCurrentIndex(index if self._profiles else -1)
        self.selector.blockSignals(False)
        self._current = -1
        self.select(self.selector.currentIndex())

    def select(self, index):
        self.save()
        self._current = index
        self.table.setRowCount(0)
        if index < 0:
            return
        for model in self._profiles[index].models:
            row = self.table.rowCount()
            self.table.insertRow(row)
            enabled = QTableWidgetItem()
            enabled.setCheckState(Qt.Checked if model.enabled else Qt.Unchecked)
            self.table.setItem(row, 0, enabled)
            self.table.setItem(row, 1, QTableWidgetItem(str(model.file)))
            section = QComboBox(self.table)
            section.setEditable(True)
            section.addItems(["", *model_corners(str(model.file))])
            section.setCurrentText(model.section)
            self.table.setCellWidget(row, 2, section)
            self.table.setItem(row, 3, QTableWidgetItem(model.label))

    def add_profile(self, duplicate=False):
        name, ok = ask_text(self, "Corner section", "Section name")
        if ok and name.strip():
            self.save()
            models = self._profiles[self._current].models if duplicate and self._current >= 0 else ()
            self._profiles.append(CornerProfile(name.strip(), models))
            self.refresh(len(self._profiles)-1)

    def rename_profile(self):
        if self._current < 0:
            return
        name, ok = ask_text(self, "Corner section", "Section name",
                            value=self._profiles[self._current].name)
        if ok and name.strip():
            self.save()
            self._profiles[self._current] = CornerProfile(name.strip(), self._profiles[self._current].models)
            self.refresh(self._current)

    def remove_profile(self):
        if self._current >= 0:
            del self._profiles[self._current]
            self.refresh()

    def add_model(self):
        if self._current < 0:
            return
        files, _ = ask_files(self, "Model files", [])
        if files:
            self.save()
            p = self._profiles[self._current]
            self._profiles[self._current] = CornerProfile(p.name, p.models + tuple(ModelEntry(Path(f)) for f in files))
            self.refresh(self._current)

    def remove_model(self):
        if self.table.currentRow() >= 0:
            self.table.removeRow(self.table.currentRow())

    def move_model(self, delta):
        row = self.table.currentRow()
        if 0 <= row + delta < self.table.rowCount() and row >= 0:
            self.save()
            p = self._profiles[self._current]
            models = list(p.models)
            models[row], models[row+delta] = models[row+delta], models[row]
            self._profiles[self._current] = CornerProfile(p.name, tuple(models))
            self.refresh(self._current)
            self.table.selectRow(row+delta)

    def profiles(self):
        self.save()
        return tuple(self._profiles)

    def accept(self):
        try:
            CornerExport("library", "mts_corner", self.profiles()).validate("spectre")
        except Exception as exc:
            notice(self, "Invalid corner profiles", str(exc))
            return
        super().accept()
