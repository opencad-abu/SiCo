"""One stable skill path and its default enablement."""

from PyQt5.QtWidgets import QCheckBox, QFormLayout, QLineEdit, QPushButton
from sico.agent_resources.schema import validate_manifest
from .chrome import SiDialog


class SkillOverrideEditor(SiDialog):
    def __init__(self, parent, row=None):
        super().__init__("单技能默认设置", parent)
        self.value = None
        self.resize(650, 170)
        form = QFormLayout()
        self.content_layout().addLayout(form)
        self.path = QLineEdit((row or {}).get("path", ""))
        self.path.setPlaceholderText("外部技能定义的绝对路径，以 /SKILL.md 结尾")
        self.enabled = QCheckBox("新会话启用此技能")
        self.enabled.setChecked((row or {}).get("enabled", True))
        form.addRow("定义路径", self.path)
        form.addRow(self.enabled)
        self.accept_button = QPushButton("加入草稿")
        self.accept_button.clicked.connect(lambda _checked=False: self.submit())
        form.addRow(self.accept_button)

    def submit(self):
        try:
            self.value = validate_manifest({"version": 2, "skill_overrides": [{
                "path": self.path.text().strip(), "enabled": self.enabled.isChecked()}]})[
                    "skill_overrides"][0]
        except ValueError:
            self.path.setPlaceholderText("请填写有效的绝对 SKILL.md 定义路径")
            return
        self.accept()
