"""Advanced turn settings; model and effort belong to the composer dropdowns."""

from PyQt5.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QPlainTextEdit

from sico.codex.turn_options import parse_output_schema
from sico.codex.access_policy import SCOPE_NOTE, default_access, validate
from sico.service.event_display import encoded

from .turn_dialog import TurnDialog
from .turn_draft_options import draft_options, model_row, store_options


class TurnSettingsDialog(TurnDialog):
    def __init__(self, window):
        super().__init__("回合设置", window)
        self.resize(560, 450)
        form = QFormLayout()
        self.mode, self.tier, self.turn_tier = QComboBox(), QComboBox(), QComboBox()
        for title, widget in (("模式", self.mode), ("服务等级", self.tier),
                              ("本轮服务等级", self.turn_tier)):
            form.addRow(title, widget)
        self.approval = QComboBox()
        self.approval.addItem("越界时询问", "on-request")
        self.approval.addItem("越界时直接拒绝", "never")
        form.addRow("Codex 执行审批", self.approval)
        self.network = QCheckBox("允许本地命令联网")
        form.addRow("Codex 命令网络", self.network)
        self.body_layout.addLayout(form)
        note = QLabel(SCOPE_NOTE + " 工作区写入包含项目目录与临时目录；完全访问不限制命令联网。")
        note.setWordWrap(True)
        self.body_layout.addWidget(note)
        self.schema = QPlainTextEdit()
        self.schema.setPlaceholderText("输出 JSON Schema（留空使用默认输出）")
        self.body_layout.addWidget(self.schema, 1)
        self.load()

    def populate(self):
        options = draft_options(self.window.input, self.catalog)
        access = options.get("access", default_access())
        self.approval.setCurrentIndex(self.approval.findData(access["approval"]))
        self.network.setChecked(access["network"])
        self.network.setEnabled(access["sandbox"] != "danger-full-access")
        row = model_row(self.catalog, options)
        self.mode.addItems(list(dict.fromkeys(["default", *self.catalog["modes"]])))
        self.mode.setCurrentText(options.get("mode", "default"))
        for combo, first, key in ((self.tier, "线程默认", "serviceTier"),
                                  (self.turn_tier, "沿用服务等级", "serviceTierForTurn")):
            combo.addItem(first, None)
            combo.addItem("default", "default")
            for tier in row.get("serviceTiers") or []:
                if tier["id"] != "default":
                    combo.addItem(tier["name"], tier["id"])
            combo.setCurrentIndex(max(0, combo.findData(options.get(key))))
        if "outputSchema" in options:
            self.schema.setPlainText(encoded(options["outputSchema"]).decode("utf-8"))

    def apply(self):
        if not self.current() or self.catalog is None:
            self.reject()
            return
        try:
            schema = parse_output_schema(self.schema.toPlainText())
        except ValueError as exc:
            self.hint.setText(str(exc))
            return
        options = draft_options(self.window.input, self.catalog)
        access = options.get("access", default_access())
        access.update(approval=self.approval.currentData(), network=self.network.isChecked())
        options["access"] = validate(access)
        options["mode"] = self.mode.currentText()
        for widget, key in ((self.tier, "serviceTier"), (self.turn_tier, "serviceTierForTurn")):
            options.pop(key, None)
            if widget.currentData() is not None:
                options[key] = widget.currentData()
        options.pop("outputSchema", None)
        if schema is not None:
            options["outputSchema"] = schema
        store_options(self.window.input, options, self.catalog)
        self.window.turn_composer.refresh()
        self.accept()
