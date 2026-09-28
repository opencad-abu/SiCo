"""Preserve raw numeric text until request validation."""

from __future__ import annotations
from PyQt5.QtWidgets import (
    QLineEdit,
)


class StringNumericEdit(QLineEdit):
    """Text-preserving numeric editor with the old spinbox test API.

    Temperature, scale, and gmin are simulator input strings at the GUI boundary.
    Keeping the original text avoids displaying padded decimal places or
    silently changing scientific notation; request validation converts the
    value to the typed ``ProcessOptions`` representation only when Run is
    pressed.
    """

    def __init__(self, unset_value: float, parent=None) -> None:
        super().__init__(parent)
        self._unset_value = unset_value
        self.setPlaceholderText("unset")

    def minimum(self) -> float:
        """Compatibility sentinel used by existing scripted GUI callers."""

        return self._unset_value

    def setValue(self, value) -> None:  # noqa: N802 - Qt-compatible spelling
        if value is None:
            self.clear()
            return
        try:
            is_unset = float(value) == self._unset_value
        except (TypeError, ValueError):
            # Preserve an invalid edit while the user moves between cells;
            # the shared request validator will report it when Run is pressed.
            is_unset = False
        if is_unset:
            self.clear()
        else:
            self.setText(str(value))

    def value(self) -> float:  # noqa: N802 - Qt-compatible spelling
        raw = self.text().strip()
        if not raw:
            return self._unset_value
        try:
            return float(raw)
        except ValueError:
            return self._unset_value
