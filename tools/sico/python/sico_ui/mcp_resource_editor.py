"""Edit one independent MCP definition using references, never secret values."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QDoubleSpinBox, QScrollArea, QVBoxLayout, QWidget,
)

from sico.agent_resources.schema import validate_manifest
from .chrome import SiDialog


class McpResourceEditor(SiDialog):
    def __init__(self, parent, row=None):
        super().__init__("编辑 MCP 服务" if row else "添加 MCP 服务", parent)
        self.value, self.row = None, row or {}
        self.resize(680, 610)
        layout = QVBoxLayout()
        self.content_layout().addLayout(layout)
        self.form = QFormLayout()
        body, scroll = QWidget(), QScrollArea()
        body.setLayout(self.form)
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        self.fields = {}
        for key, title in (("id", "服务 ID"), ("name", "显示名称")):
            self.line(key, title)
        self.transport = QComboBox()
        self.transport.addItem("STDIO 本地进程", "stdio")
        self.transport.addItem("Streamable HTTP", "streamable_http")
        self.form.addRow("连接类型", self.transport)
        for key, title in (("command", "可执行命令"), ("cwd", "工作目录（绝对路径）"),
                           ("url", "服务 URL"), ("credential_ref", "Bearer 环境变量名")):
            self.line(key, title)
        for key, title in (("args", "参数（每行一个）"), ("env_refs", "环境引用（目标名=来源名）"),
                           ("enabled_tools", "允许工具（每行一个）")):
            value = self.fields[key] = QPlainTextEdit()
            value.setMaximumHeight(64)
            data = self.row.get(key, [])
            value.setPlainText("\n".join(k + "=" + v for k, v in data.items()) if
                               key == "env_refs" and data else "\n".join(data))
            self.form.addRow(title, value)
        self.filter_tools = QCheckBox("限制工具清单；空清单表示禁用全部工具")
        self.filter_tools.setChecked("enabled_tools" in self.row)
        self.form.addRow("", self.filter_tools)
        self.source = QComboBox()
        self.source.addItem("工程", "project")
        self.source.addItem("用户", "user")
        self.source.setCurrentIndex(self.source.findData(self.row.get("source", "project")))
        self.form.addRow("来源", self.source)
        for key, title, default in (("startup_timeout_sec", "启动超时（秒）", 10),
                                    ("tool_timeout_sec", "调用超时（秒）", 60)):
            value = self.fields[key] = QDoubleSpinBox()
            value.setDecimals(3)
            value.setRange(0.001, 600)
            value.setValue(self.row.get(key, default))
            self.form.addRow(title, value)
        self.enabled, self.required = QCheckBox("新会话启用"), QCheckBox("连接失败阻止新会话启动")
        self.enabled.setChecked(self.row.get("enabled", True))
        self.required.setChecked(self.row.get("required", False))
        self.form.addRow(self.enabled, self.required)
        self.message = QLabel("凭据由工程服务环境提供；此处仅填写变量名。OAuth 尚未支持。")
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel, accept = QPushButton("取消"), QPushButton("加入草稿")
        cancel.clicked.connect(lambda _checked=False: self.reject())
        accept.clicked.connect(lambda _checked=False: self.submit())
        actions.addWidget(cancel)
        actions.addWidget(accept)
        layout.addLayout(actions)
        self.transport.setCurrentIndex(self.transport.findData(self.row.get("transport", "stdio")))
        self.transport.currentIndexChanged.connect(lambda _index: self.render())
        self.render()

    def line(self, key, title):
        value = self.fields[key] = QLineEdit(str(self.row.get(key, "")))
        value.setMaxLength(4096)
        self.form.addRow(title, value)

    def render(self):
        stdio = self.transport.currentData() == "stdio"
        for key in ("command", "args", "cwd", "env_refs", "url", "credential_ref"):
            visible = stdio == (key in {"command", "args", "cwd", "env_refs"})
            self.fields[key].setVisible(visible)
            self.form.labelForField(self.fields[key]).setVisible(visible)

    def submit(self):
        def text(key):
            return self.fields[key].text().strip()
        def lines(key):
            return self.fields[key].toPlainText().splitlines()
        row = {"id": text("id"), "name": text("name"), "transport": self.transport.currentData(),
               "source": self.source.currentData(), "enabled": self.enabled.isChecked(),
               "required": self.required.isChecked()}
        try:
            if row["transport"] == "stdio":
                refs = {}
                for line in lines("env_refs"):
                    key, value = line.split("=", 1)
                    if key.strip() in refs:
                        raise ValueError("环境引用名称重复")
                    refs[key.strip()] = value.strip()
                row.update(command=text("command"), args=lines("args"), cwd=text("cwd"), env_refs=refs)
            else:
                row["url"] = text("url")
                if text("credential_ref"):
                    row["credential_ref"] = text("credential_ref")
            for key in ("startup_timeout_sec", "tool_timeout_sec"):
                row[key] = self.fields[key].value()
            if self.filter_tools.isChecked():
                row["enabled_tools"] = lines("enabled_tools")
            self.value = validate_manifest({"version": 2, "mcp_servers": [row]})["mcp_servers"][0]
        except (ValueError, TypeError):
            self.message.setText("字段无效，请核对绝对路径、服务 ID、URL 和环境变量引用格式。")
            return
        self.accept()
