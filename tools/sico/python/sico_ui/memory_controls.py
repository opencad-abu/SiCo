"""One dialog for the current project's native memory settings."""

from PyQt5.QtWidgets import QCheckBox, QLabel, QSizePolicy

from .form_styles import checkbox_stylesheet
from .thread_control_base import ThreadControlDialog


class MemoryDialog(ThreadControlDialog):
    def __init__(self, window):
        super().__init__(window, "工程记忆")

    def build(self):
        self.summary.hide()
        self.memory_flags = {}
        for key, text in (("enabled", "启用工程记忆"), ("generate", "生成记忆"),
                          ("use", "使用记忆")):
            flag = QCheckBox(text)
            flag.setStyleSheet(checkbox_stylesheet())
            self.memory_flags[key] = flag
            self.body.addWidget(flag)
        self.memory_flags["enabled"].toggled.connect(self.memory_enabled)
        self.memory_enabled(False)
        self.memory_state = QLabel("尚未读取")
        self.memory_state.setWordWrap(True)
        self.body.addWidget(self.memory_state)
        note = QLabel("记忆保存在当前工程工作区，同一工程的新会话共享，不同工程相互隔离。"
                      "旧会话继续使用原有独立记忆。启用后可能产生额外模型用量。")
        note.setWordWrap(True)
        note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.body.addWidget(note)
        self.apply = self.button(self.body, "应用记忆设置", lambda: {"kind": "memory",
            "settings": {key: flag.isChecked() for key, flag in self.memory_flags.items()}})
        self.body.addStretch(1)

    def memory_enabled(self, enabled):
        for key in ("generate", "use"):
            self.memory_flags[key].setEnabled(enabled)
            if not enabled:
                self.memory_flags[key].setChecked(False)

    def render_page(self, snapshot, initial):
        memory = snapshot.get("memory", {})
        effective = memory.get("effective")
        self.memory_state.setText("实际配置：" + ("，".join(
            label + ("已启用" if effective.get(key) else "已关闭")
            for key, label in (("enabled", "记忆"), ("generate", "生成"), ("use", "使用")))
            if effective is not None and memory.get("verified") else "尚未验证"))
        if initial:
            for key, flag in self.memory_flags.items():
                flag.setChecked(
                    (memory.get("configured") or effective or {}).get(key) is True)
        available = not snapshot.get("busy", False) and bool(snapshot.get("thread_id"))
        self.memory_flags["enabled"].setEnabled(available)
        child_enabled = available and self.memory_flags["enabled"].isChecked()
        self.memory_flags["generate"].setEnabled(child_enabled)
        self.memory_flags["use"].setEnabled(child_enabled)
        self.apply.setEnabled(available)
