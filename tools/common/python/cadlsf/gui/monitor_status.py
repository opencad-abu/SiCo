"""Monitor status and bounded diagnostic presentation."""

from cadgui.palette import active
from PyQt5.QtWidgets import QLabel, QSizePolicy, QStatusBar


class MonitorStatus(QStatusBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.kind = "stale"
        self.state_label = QLabel(self)
        self.state_label.setObjectName("monitorStateLabel")
        self.diagnostic_label = QLabel(self)
        self.diagnostic_label.setObjectName("diagnosticLabel")
        self.diagnostic_label.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Preferred
        )
        self.addWidget(self.state_label)
        self.addPermanentWidget(self.diagnostic_label, 1)

    def show_status(self, kind: str, label: str, diagnostic: str = "") -> None:
        self.kind = kind
        # 状态色和桌面状态点共用一套（就绪/进行中/注意/过期/故障）。
        palette = active()
        colors = {
            "ready": palette.state_ready,
            "refreshing": palette.state_busy,
            "partial": palette.state_warn,
            "stale": palette.state_stale,
            "error": palette.state_error,
        }
        self.state_label.setText(label)
        self.state_label.setStyleSheet(
            f"color: {colors.get(kind, palette.text)}; font-weight: 600;"
        )
        summary = diagnostic.strip()
        if len(summary) > 360:
            summary = summary[:357] + "..."
        self.diagnostic_label.setText(summary)
        self.diagnostic_label.setToolTip(diagnostic)
