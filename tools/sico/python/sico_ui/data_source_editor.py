"""Edit one project structured-data root; the source itself is never modified."""

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from sico.agent_resources.data_sources import LABELS
from sico.agent_resources.schema import path_value
from .chrome import SiDialog
from .si_prompt import SiFilePrompt


class DataSourceEditor(SiDialog):
    def __init__(self, parent, kind, path=""):
        super().__init__("编辑" + LABELS[kind], parent)
        self.kind, self.value, self.picker = kind, None, None
        self.resize(640, 220)
        layout = QVBoxLayout()
        self.content_layout().addLayout(layout)
        layout.addWidget(QLabel(
            "工艺数据可选标准 index.json、package.json 或数据目录；"
            "模板数据必须是目录。"
        ))
        row = QHBoxLayout()
        self.path = QLineEdit(str(path or ""))
        self.path.setPlaceholderText("绝对路径")
        self.path.setMaxLength(4096)
        row.addWidget(self.path, 1)
        if kind == "pdk_data":
            for text, mode in (("文件…", QFileDialog.ExistingFile),
                               ("目录…", QFileDialog.Directory)):
                button = QPushButton(text)
                button.clicked.connect(
                    lambda _checked=False, m=mode: self.browse(m))
                row.addWidget(button)
        else:
            button = QPushButton("目录…")
            button.clicked.connect(
                lambda _checked=False: self.browse(QFileDialog.Directory))
            row.addWidget(button)
        layout.addLayout(row)
        self.message = QLabel()
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel, accept = QPushButton("取消"), QPushButton("应用")
        cancel.clicked.connect(lambda _checked=False: self.reject())
        accept.clicked.connect(lambda _checked=False: self.submit())
        actions.addWidget(cancel)
        actions.addWidget(accept)
        layout.addLayout(actions)

    def browse(self, mode):
        if self.picker is not None:
            self.picker.raise_()
            return
        picker = self.picker = SiFilePrompt(
            "选择工艺数据文件" if mode == QFileDialog.ExistingFile else "选择数据目录",
            ["JSON (*.json)", "所有文件 (*)"] if mode == QFileDialog.ExistingFile else [],
            self, directory=self.path.text(), mode=mode)

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
        try:
            value = path_value(self.path.text().strip(), resolve=False)
            target = Path(value)
            if self.kind == "circuit_templates" and target.exists() and not target.is_dir():
                raise ValueError("模板数据必须是目录")
            if self.kind == "pdk_data" and target.exists() and not (
                    target.is_file() or target.is_dir()):
                raise ValueError("工艺数据必须是文件或目录")
        except (OSError, RuntimeError, ValueError) as exc:
            self.message.setText(str(exc))
            return
        self.value = value
        self.accept()
