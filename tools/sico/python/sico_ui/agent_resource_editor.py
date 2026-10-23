"""Edit one external resource definition; paths remain external."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from sico.agent_resources.schema import validate_manifest
from .chrome import SiDialog
from .si_prompt import SiFilePrompt

LABELS = {"skill_roots": "技能目录", "plugins": "本地插件", "capability_roots": "能力目录"}


class ResourceEditor(SiDialog):
    def __init__(self, parent, kind, row=None):
        super().__init__("编辑" + LABELS[kind] if row else "添加" + LABELS[kind], parent)
        self.kind, self.value = kind, None
        self.picker = None
        self.resize(600, 300)
        layout = QVBoxLayout()
        self.content_layout().addLayout(layout)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        layout.addLayout(form)
        self.identity = QLineEdit()
        self.identity.setPlaceholderText("例如 design-skills")
        self.identity.setMaxLength(128)
        form.addRow("资源 ID", self.identity)
        self.path = QLineEdit()
        self.path.setPlaceholderText("资源的绝对路径")
        self.path.setMaxLength(4096)
        location = QHBoxLayout()
        location.addWidget(self.path, 1)
        browse = QPushButton("浏览…")
        browse.clicked.connect(lambda _checked=False: self.browse())
        location.addWidget(browse)
        form.addRow("清单文件" if kind == "plugins" else "目录", location)
        self.name = QLineEdit()
        self.name.setMaxLength(128)
        if kind == "plugins":
            self.name.setPlaceholderText("与插件清单中的名称一致")
            form.addRow("插件名称", self.name)
        self.source = QComboBox()
        self.source.addItem("工程", "project")
        self.source.addItem("用户", "user")
        form.addRow("来源", self.source)
        self.enabled = QCheckBox("在新会话中启用")
        self.enabled.setChecked(True)
        form.addRow("", self.enabled)
        self.message = QLabel()
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel, accept = QPushButton("取消"), QPushButton("加入草稿")
        cancel.clicked.connect(lambda _checked=False: self.reject())
        accept.clicked.connect(lambda _checked=False: self.submit())
        buttons.addWidget(cancel)
        buttons.addWidget(accept)
        layout.addLayout(buttons)
        if row:
            self.identity.setText(row["id"])
            self.path.setText(row["path"])
            self.source.setCurrentIndex(self.source.findData(row["source"]))
            self.enabled.setChecked(row["enabled"])
            self.name.setText(row.get("name", ""))

    def browse(self):
        if self.picker is not None:
            self.picker.raise_()
            return
        plugin = self.kind == "plugins"
        picker = self.picker = SiFilePrompt(
            "选择 marketplace.json" if plugin else "选择外部目录",
            ["JSON (*.json)"] if plugin else [], self, directory=self.path.text(),
            mode=QFileDialog.ExistingFile if plugin else QFileDialog.Directory)
        def chosen(key):
            paths = picker.picker.selectedFiles()
            if key == "accept" and paths:
                self.path.setText(paths[0])
        def finished(_result):
            self.picker = None
            picker.deleteLater()
        picker.chosen.connect(chosen)
        picker.finished.connect(finished)
        picker.open()

    def submit(self):
        row = {"id": self.identity.text().strip(), "path": self.path.text().strip(),
               "source": self.source.currentData(), "enabled": self.enabled.isChecked(),
               "access": "read" if self.kind == "skill_roots" else "execute"}
        if self.kind == "plugins":
            row["name"] = self.name.text().strip()
        try:
            self.value = validate_manifest({"version": 2, self.kind: [row]})[self.kind][0]
        except (ValueError, OSError, RuntimeError) as exc:
            self.message.setText(str(exc))
            return
        self.accept()
