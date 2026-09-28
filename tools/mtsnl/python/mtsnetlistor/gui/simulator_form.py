"""Construct simulator controls with raw text and ordered options."""

from __future__ import annotations
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)


from .numeric_edit import StringNumericEdit


class SimulatorForm(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("Simulator Options", parent)
        box = self
        form = QFormLayout(box)
        self.simulator = QComboBox(box)
        self.simulator.addItems(["spectre", "hspiceD"])
        form.addRow("Simulator", self.simulator)

        def optional_spin(minimum: float, maximum: float) -> QDoubleSpinBox:
            # Reserve a value just outside the usable domain as the explicit
            # unset sentinel.  This keeps -273.15 C and the smallest positive
            # scale/reltol values representable instead of conflating them
            # with "unset".
            spin = QDoubleSpinBox(box)
            spin.setDecimals(9)
            spin.setRange(minimum, maximum)
            spin.setSpecialValueText("unset")
            spin.setValue(minimum)
            return spin

        self.temp = StringNumericEdit(-273.150001, box)
        self.temperature_mode = QComboBox(box)
        self.temperature_mode.addItem("Fixed locally (value or source default)", "fixed")
        self.temperature_mode.addItem("Inherit ADE top temperature", "inherit")
        temperature_row = QWidget(box)
        temperature_layout = QHBoxLayout(temperature_row)
        temperature_layout.setContentsMargins(0, 0, 0, 0)
        temperature_layout.addWidget(self.temp)
        temperature_layout.addWidget(self.temperature_mode)
        form.addRow("Temperature", temperature_row)
        self.scale = StringNumericEdit(0.0, box)
        form.addRow("Scale", self.scale)
        self.gmin = StringNumericEdit(0.0, box)
        form.addRow("Gmin", self.gmin)
        # These legacy attributes remain available for old scripted tests and
        # saved form integrations, but are intentionally not placed in the
        # visible form.  Users can set equivalent simulator options in the
        # ordered Advanced Options table instead.
        self.tnom = optional_spin(-273.150001, 10000.0)
        self.scalem = optional_spin(0.0, 1_000_000.0)
        self.reltol = optional_spin(0.0, 1_000_000.0)
        for legacy_spin in (self.tnom, self.scalem, self.reltol):
            legacy_spin.hide()

        # Keep advanced options as a user-editable table. Automatic PDK
        # defaults loading intentionally leaves this table untouched for now;
        # the complete ASI snapshot remains available in defaults.json.
        advanced = QWidget(box)
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.options_table = QTableWidget(0, 5, advanced)
        self.options_table.setHorizontalHeaderLabels(
            ["Enabled", "Name", "Type", "Value", "Enum Values"]
        )
        self.options_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        advanced_layout.addWidget(self.options_table)
        option_buttons = QHBoxLayout()
        self.add_option = QPushButton("Add")
        self.remove_option = QPushButton("Remove")
        self.duplicate_option = QPushButton("Duplicate")
        self.move_option_up = QPushButton("Move Up")
        self.move_option_down = QPushButton("Move Down")
        for button in (self.add_option, self.remove_option, self.duplicate_option, self.move_option_up, self.move_option_down):
            option_buttons.addWidget(button)
        option_buttons.addStretch(1)
        advanced_layout.addLayout(option_buttons)
        form.addRow("Advanced Options", advanced)

