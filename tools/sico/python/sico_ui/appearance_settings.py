"""The local appearance page: palette, body-text size and interface-label size."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from .appearance import (
    LABEL_MAX,
    LABEL_MIN,
    TEXT_MAX,
    TEXT_MIN,
    THEMES,
    load_appearance,
    save_appearance,
    system_point_size,
)
from .appearance_runtime import apply_appearance
from .chrome import SiDialog


class AppearanceSettingsDialog(SiDialog):
    """Edit the visual preferences stored with the current project workspace."""

    def __init__(self, window):
        super().__init__("外观设置", window)
        self.window = window
        self.saved = False
        self.resize(520, 320)
        preferences = load_appearance(
            window.settings, default_point_size=system_point_size())
        layout = QVBoxLayout()
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)
        self.content_layout().addLayout(layout)
        explanation = QLabel(
            "新工程默认使用黑暗主题，保留红棕色强调色，并使用深色底面与浅色文字；"
            "明亮主题仍可切换。"
            "正文字号作用于聊天、汇报、JSON/Markdown 文本和终端；标签字号作用于界面控件。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        self.theme = QComboBox()
        for name, label in THEMES.items():
            self.theme.addItem(label, name)
        self.theme.setCurrentIndex(max(0, self.theme.findData(preferences.theme)))
        form.addRow("主题", self.theme)
        self.text_size = self._size_box(TEXT_MIN, TEXT_MAX, preferences.text_point_size)
        form.addRow("正文字号", self.text_size)
        self.label_size = self._size_box(
            LABEL_MIN, LABEL_MAX, preferences.label_point_size)
        form.addRow("界面标签字号", self.label_size)
        layout.addLayout(form)
        self.hint = QLabel("保存后立即应用到当前窗口，并用于本工程以后打开的 SiCo 窗口。")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        layout.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText("保存")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    @staticmethod
    def _size_box(minimum, maximum, value):
        box = QSpinBox()
        box.setRange(minimum, maximum)
        box.setSuffix(" pt")
        box.setValue(value)
        return box

    def preferences(self):
        from .appearance import AppearancePreferences

        return AppearancePreferences(
            theme=self.theme.currentData(),
            text_point_size=self.text_size.value(),
            label_point_size=self.label_size.value(),
        )

    def save(self):
        try:
            preferences = self.preferences().validated()
            save_appearance(self.window.settings, preferences)
            self.window.appearance = preferences
            apply_appearance(preferences)
        except (OSError, ValueError) as exc:
            self.hint.setText(str(exc))
            return
        self.saved = True
        self.accept()


def show_appearance_settings(window):
    """Open one appearance page per workspace window."""
    previous = getattr(window, "appearance_settings_dialog", None)
    if previous is not None and previous.isVisible():
        previous.raise_()
        previous.activateWindow()
        return
    window.appearance_settings_dialog = AppearanceSettingsDialog(window)
    window.appearance_settings_dialog.show()
