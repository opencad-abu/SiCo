"""Stage one local or remote knowledge definition in the shared manifest."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)

from sico.agent_resources.schema import require_supported, validate_manifest
from .chrome import SiDialog

DEFAULT_URLS = {"openai_docs_mcp": "https://developers.openai.com/mcp", "jedai_aai_mcp": ""}


class KnowledgeEditor(SiDialog):
    def __init__(self, parent, row=None, kind="local"):
        super().__init__("编辑知识源" if row else "添加知识源", parent)
        self.row, self.value = row or {}, None
        self.kind = self.row.get("type", kind)
        self.resize(650, 590)
        outer = QVBoxLayout()
        self.content_layout().addLayout(outer)
        scroll, body = QScrollArea(), QWidget()
        scroll.setWidgetResizable(True)
        self.form = QFormLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.fields = {}
        for key, label in (("id", "知识源 ID"), ("name", "显示名称")):
            self.line(key, label)
        if self.kind == "local":
            self.line("path", "资料目录或文件（绝对路径）")
            self.form.addRow(QLabel("文本采用 UTF-8；扫描 PDF 标记为需 OCR。"))
            self.formats = {}
            for key, label in (("text", "文本"), ("markdown", "Markdown"), ("pdf", "PDF")):
                field = self.formats[key] = QCheckBox(label)
                field.setChecked(key in self.row.get("formats", ["text", "markdown", "pdf"]))
                self.form.addRow("支持格式", field)
            for key, label in (("include", "包含范围（每行一个 glob，空为全部）"),
                               ("exclude", "排除范围（每行一个 glob）")):
                field = self.fields[key] = QPlainTextEdit("\n".join(self.row.get(key, [])))
                field.setMaximumHeight(65)
                self.form.addRow(label, field)
            self.form.addRow(QLabel("更新方式：手动；移除配置保留原文和索引。"))
        else:
            self.adapter = QComboBox()
            for text, value in (("OpenAI 官方文档 MCP", "openai_docs_mcp"),
                                ("JedAI AAI（向量检索）", "jedai_aai_mcp")):
                self.adapter.addItem(text, value)
            self.adapter.setCurrentIndex(max(0, self.adapter.findData(
                self.row.get("adapter", "openai_docs_mcp"))))
            self.form.addRow("适配器", self.adapter)
            self.line("url", "服务地址", DEFAULT_URLS[self.adapter.currentData()])
            self.collections = QStackedWidget()
            self.collection = QComboBox()
            for text, value in (("全部官方文档", "all"), ("Codex 文档", "codex"), ("API 文档", "api")):
                self.collection.addItem(text, value)
            if self.adapter.currentData() == "openai_docs_mcp":
                self.collection.setCurrentIndex(
                    max(0, self.collection.findData(self.row.get("collection", "all"))))
            self.collection_text = QLineEdit(str(self.row.get("collection", "")))
            self.collection_text.setMaxLength(4096)
            self.collection_text.setPlaceholderText("填写管理员共享给此账号的 JedAI 类别名")
            self.collections.addWidget(self.collection)
            self.collections.addWidget(self.collection_text)
            # 适配器切换用的堆叠容器只占单行高度，避免吸走表单多余的竖向空间。
            self.collections.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            self.form.addRow("知识集合", self.collections)
            self.line("credential_ref", "Bearer 环境变量名（公开服务留空）")
            self.fields["credential_ref"].setPlaceholderText("JedAI 建议 JEDAI_ACCESS_TOKEN")
            self.timeout = QDoubleSpinBox()
            self.timeout.setRange(0.1, 45)
            self.timeout.setValue(self.row.get("timeout_sec", 30))
            self.form.addRow("单来源搜索超时（秒）", self.timeout)
            self.adapter.currentIndexChanged.connect(lambda _index: self.adapter_changed())
            self.adapter_changed()
        self.source = QComboBox()
        self.source.addItem("工程", "project")
        self.source.addItem("用户", "user")
        self.source.setCurrentIndex(self.source.findData(self.row.get("source", "project")))
        self.form.addRow("来源", self.source)
        self.enabled = QCheckBox("新会话启用")
        self.enabled.setChecked(self.row.get("enabled", True))
        self.form.addRow(self.enabled)
        self.message = QLabel("资料仅用于解释与查证；PDK 器件资格和 CDF 规则以结构化接口为准。")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
        outer.addWidget(self.message)
        actions = QHBoxLayout()
        actions.addStretch()
        for text, action in (("取消", self.reject), ("加入草稿", self.submit)):
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, a=action: a())
            actions.addWidget(button)
        outer.addLayout(actions)

    def line(self, key, label, default=""):
        value = self.fields[key] = QLineEdit(str(self.row.get(key, default)))
        value.setMaxLength(4096)
        self.form.addRow(label, value)

    def adapter_changed(self):
        """One bounded collection per definition: official whitelist or JedAI category."""
        adapter = self.adapter.currentData()
        self.collections.setCurrentIndex(0 if adapter == "openai_docs_mcp" else 1)
        field = self.fields["url"]
        if not field.text().strip() or field.text().strip() in set(DEFAULT_URLS.values()):
            field.setText(DEFAULT_URLS[adapter])
        field.setPlaceholderText("" if adapter == "openai_docs_mcp"
                                 else "JedAI 实例的 AAI MCP 端点，如 http://<host>:<port>/api/aai/mcp/")

    def submit(self):
        def text(key):
            return self.fields[key].text().strip()
        row = {"id": text("id"), "name": text("name"), "source": self.source.currentData(),
               "enabled": self.enabled.isChecked(), "type": self.kind}
        if self.kind == "local":
            row.update(path=text("path"), formats=[k for k, v in self.formats.items() if v.isChecked()],
                       update="manual", **{k: self.fields[k].toPlainText().splitlines() for k in ("include", "exclude")})
        else:
            adapter = self.adapter.currentData()
            collection = (self.collection.currentData() if adapter == "openai_docs_mcp"
                          else self.collection_text.text().strip())
            if not collection:
                self.message.setText("请填写 JedAI 类别名（collection）")
                return
            row.update(adapter=adapter, url=text("url"), collection=collection,
                       timeout_sec=self.timeout.value())
            if text("credential_ref"):
                row["credential_ref"] = text("credential_ref")
        try:
            value = validate_manifest({"version": 2, "knowledge_sources": [row]})
            require_supported(value)
            self.value = value["knowledge_sources"][0]
        except (ValueError, TypeError) as exc:
            self.message.setText(str(exc))
            return
        self.accept()
