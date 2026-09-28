"""Panel-local notices for background failures; details open through InfoApp."""

from PyQt5.QtWidgets import QPushButton, QVBoxLayout, QWidget


class PassiveNotices(QWidget):
    """Background refresh never opens a dialog or changes the conversation status.

    Each owner replaces or clears its own keyed notice. Only an explicit click
    opens the full, copyable message in InfoApp.
    """

    def __init__(self, notices, parent=None):
        super().__init__(parent)
        self.notices = notices
        self.messages = {}
        self._buttons = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.hide()

    def set(self, key, title, message, *, level="warning"):
        value = (str(title), str(message), level)
        if self.messages.get(key) == value:
            return
        self.messages[key] = value
        button = self._buttons.get(key)
        if button is None:
            button = QPushButton(self)
            button.clicked.connect(lambda _checked=False: self.open(key))
            self.layout().addWidget(button)
            self._buttons[key] = button
        button.setText(value[0] + " · 查看详情")
        button.setToolTip(value[1])
        self.show()

    def clear(self, key):
        self.messages.pop(key, None)
        button = self._buttons.pop(key, None)
        if button is not None:
            self.layout().removeWidget(button)
            button.hide()
            button.deleteLater()
        self.setVisible(bool(self.messages))

    def open(self, key):
        value = self.messages.get(key)
        if value is not None:
            getattr(self.notices, value[2])(*value[:2])
