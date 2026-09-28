"""Construct target library/view and independent overwrite controls."""

from __future__ import annotations
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QWidget,
)


class PublicationForm(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Simulation Views", parent)
        box = self
        form = QFormLayout(box)
        self.publish_symbol = QCheckBox("Generate symbol view")
        self.overwrite_symbol_view = QCheckBox("Overwrite if exist")
        self.publish_text = QCheckBox("Generate netlist view")
        self.overwrite_netlist_view = QCheckBox("Overwrite if exist")
        generate_width = max(
            self.publish_symbol.sizeHint().width(),
            self.publish_text.sizeHint().width(),
        )
        self.publish_symbol.setFixedWidth(generate_width)
        self.publish_text.setFixedWidth(generate_width)

        symbol_row = QWidget(box)
        symbol_layout = QHBoxLayout(symbol_row)
        symbol_layout.setContentsMargins(0, 0, 0, 0)
        symbol_layout.addWidget(self.publish_symbol)
        symbol_layout.addWidget(self.overwrite_symbol_view)
        symbol_layout.addStretch(1)
        text_row = QWidget(box)
        text_layout = QHBoxLayout(text_row)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.addWidget(self.publish_text)
        text_layout.addWidget(self.overwrite_netlist_view)
        text_layout.addStretch(1)
        form.addRow(symbol_row)
        form.addRow(text_row)
        self.target_library = QComboBox()
        self.target_library.setEditable(False)
        form.addRow("Target Library", self.target_library)
        self.target_cell = QLineEdit()
        self.target_cell.setPlaceholderText("source cell")
        form.addRow("Target Cell", self.target_cell)
        # Target selection is meaningful only when at least one publication
        # view is requested.  Keeping these controls disabled also prevents a
        # stale target choice from being mistaken for a mutation request.
        self._target_widgets = (self.target_library, self.target_cell)

