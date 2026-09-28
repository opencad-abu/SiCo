"""Attachment draft editor; turn settings are owned by separate controls."""

from copy import deepcopy

from PyQt5.QtCore import QSize
from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QListWidget,
    QStyle,
    QToolButton,
)

from .glyphs import INPUT_GLYPH_SIZE, paperclip_icon
from .si_prompt import ask_choice, ask_file, ask_multiline
from .turn_dialog import TurnDialog
from .turn_draft_options import draft_options, model_row


class AttachmentDialog(TurnDialog):
    def __init__(self, window):
        super().__init__("附件", window)
        self.inputs = deepcopy(window.input.turn_inputs)
        self.resize(600, 380)
        self.rows = QListWidget()
        self.body_layout.addWidget(self.rows, 1)
        bar = QHBoxLayout()
        self.kind = QComboBox()
        for label, kind in (("图片文件", "localImage"), ("音频文件 (WAV)", "localAudio"),
                            ("图片 data URL", "image"), ("音频 data URL", "audio"),
                            ("技能", "skill"), ("文件引用", "mention")):
            self.kind.addItem(label, kind)
        self.detail = QComboBox()
        self.detail.addItems(["auto", "high", "original"])
        self.detail.setToolTip("图片精度")
        self.kind.currentIndexChanged.connect(self.kind_changed)
        bar.addWidget(self.kind, 1)
        bar.addWidget(self.detail)
        self.add, remove = QToolButton(), QToolButton()
        self.add.setIcon(paperclip_icon())
        self.add.setIconSize(QSize(INPUT_GLYPH_SIZE, INPUT_GLYPH_SIZE))
        remove.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self.add.setToolTip("添加附件（最多 8 项）")
        remove.setToolTip("移除选中附件")
        self.add.setEnabled(False)
        self.add.clicked.connect(self.add_input)
        remove.clicked.connect(self.remove_input)
        bar.addWidget(self.add)
        bar.addWidget(remove)
        self.body_layout.addLayout(bar)
        self.render_inputs()
        self.load()

    def populate(self):
        row = model_row(self.catalog, draft_options(self.window.input, self.catalog))
        for index in range(self.kind.count()):
            modality = {"image": "image", "localImage": "image", "audio": "audio",
                        "localAudio": "audio"}.get(self.kind.itemData(index))
            self.kind.model().item(index).setEnabled(
                modality is None or modality in (row.get("inputModalities") or []))
        if not self.kind.model().item(self.kind.currentIndex()).isEnabled():
            self.kind.setCurrentIndex(self.kind.findData("mention"))
        self.kind_changed()

    def kind_changed(self, _index=0):
        self.detail.setVisible(self.kind.currentData() in {"image", "localImage"})
        self.add.setEnabled(self.catalog is not None and len(self.inputs) < 8)

    def render_inputs(self):
        self.rows.clear()
        for row in self.inputs:
            path = row.get("path", "data URL")
            kind = self.kind.itemText(self.kind.findData(row["type"]))
            self.rows.addItem(kind + " · " + row.get("name", path.rsplit("/", 1)[-1]))
            self.rows.item(self.rows.count() - 1).setToolTip(path)
        self.kind_changed()

    def add_input(self, _checked=False):
        if not self.current() or self.catalog is None or len(self.inputs) >= 8:
            return
        kind = self.kind.currentData()
        if not self.kind.model().item(self.kind.currentIndex()).isEnabled():
            self.hint.setText("当前模型未声明支持此媒体类型")
            return
        row = {"type": kind}
        if kind == "skill":
            skills = [s for s in self.catalog.get("skills", []) if s["enabled"]]
            labels = [s["name"] + " · " + s["path"] for s in skills]
            value, ok = ask_choice(self, "技能", "已启用技能", labels)
            if not ok or not value:
                return
            item = skills[labels.index(value)]
            row.update(name=item["name"], path=item["path"])
        elif kind in {"image", "audio"}:
            value, ok = ask_multiline(self, "媒体 data URL", "data URL")
            if not ok or not value:
                return
            row["url"] = value
        else:
            filters = {"localImage": "Images (*.png *.jpg *.jpeg *.gif *.webp)",
                       "localAudio": "PCM WAV (*.wav)"}
            path, _ = ask_file(self, "选择附件", [filters.get(kind, "")])
            if not path:
                return
            row["path"] = path
            if kind == "mention":
                row["name"] = path.rsplit("/", 1)[-1]
        if kind in {"image", "localImage"}:
            row["detail"] = self.detail.currentText()
        self.inputs.append(row)
        self.render_inputs()

    def remove_input(self, _checked=False):
        index = self.rows.currentRow()
        if 0 <= index < len(self.inputs):
            self.inputs.pop(index)
            self.render_inputs()

    def apply(self):
        if not self.current() or self.catalog is None:
            self.reject()
            return
        if self.window.input.turn_inputs != self.inputs:
            self.window.input.turn_inputs = deepcopy(self.inputs)
            self.window.input._revise_draft()
        self.window.turn_composer.refresh()
        self.accept()
