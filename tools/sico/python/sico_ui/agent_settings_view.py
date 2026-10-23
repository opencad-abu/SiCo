"""Settings categories, session resource status and read-only configuration comparison."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit, QListWidget, QPushButton,
    QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from cadai.mcp_server import SERVER_ID
from sico.agent_resources.snapshot import difference
from .agent_resource_page import KINDS, ResourcePage
from .data_source_page import DataSourcePage

DRIFT = {"path_changed": "路径已变化", "became_available": "资源现已可用",
         "unavailable": "资源已不可用", "plugin_identity_changed": "插件身份已变化",
         "plugin_tools_changed": "插件工具已变化", "plugin_content_changed": "插件内容已变化",
         "legacy_tools_unverified": "历史工具尚未验证"}

CATEGORIES = ("知识源", "MCP 与插件", "技能", "数据源")


def label(text=""):
    value = QLabel(text)
    value.setWordWrap(True)
    value.setTextFormat(Qt.PlainText)
    value.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return value


def path_label():
    value = QLineEdit()
    value.setReadOnly(True)
    value.setFrame(False)
    value.setStyleSheet("border: none; background: transparent;")
    value.textChanged.connect(lambda text: value.setToolTip(text))
    return value


class SettingsView(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSizeConstraint(QLayout.SetMinAndMaxSize)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self.caption = path_label()
        layout.addWidget(self.caption)
        layout.addWidget(label("工程默认用于新会话；外部清单可覆盖它。已有会话保留原来的资源选择。"))
        model = QHBoxLayout()
        model.addWidget(QLabel("模型后端"))
        self.backend = QComboBox()
        self.backend.addItem("Codex", "codex")
        self.backend.addItem("Python", "python")
        self.backend.setMinimumWidth(140)
        model.addWidget(self.backend)
        model.addWidget(QLabel("协议"))
        self.provider = QComboBox()
        self.provider.setMinimumWidth(160)
        model.addWidget(self.provider)
        model.addStretch()
        self.apply_model = QPushButton("应用")
        self.apply_model.setMinimumWidth(80)
        model.addWidget(self.apply_model)
        layout.addLayout(model)
        self.model_note = label(
            "默认 Codex / Responses；保存后对新会话生效，已有会话保持原配置。"
        )
        layout.addWidget(self.model_note)
        self.backend.currentIndexChanged.connect(
            lambda _index: self.set_model(self.backend.currentData())
        )
        self.set_model("codex")
        mode = QHBoxLayout()
        mode.addWidget(QLabel("查看"))
        self.mode = QComboBox()
        for text, value in (("工程默认 · 可编辑", "project_defaults"),
                            ("当前来源默认 · 只读", "selected_defaults"),
                            ("当前会话 · 只读", "session")):
            self.mode.addItem(text, value)
        mode.addWidget(self.mode)
        mode.addStretch()
        self.check = QPushButton("隔离检查…")
        mode.addWidget(self.check)
        self.reload = QPushButton("重新读取")
        mode.addWidget(self.reload)
        layout.addLayout(mode)
        self.source = path_label()
        layout.addWidget(self.source)
        self.summary = label()
        layout.addWidget(self.summary)
        layout.addWidget(self._session_bar())
        self.compare = QPushButton("查看配置差异")
        layout.addWidget(self.compare, 0, Qt.AlignLeft)
        split = QHBoxLayout()
        self.categories = QListWidget()
        self.categories.setObjectName("settingsCategories")
        self.categories.setFixedWidth(150)
        self.stack = QStackedWidget()
        split.addWidget(self.categories)
        split.addWidget(self.stack, 1)
        layout.addLayout(split, 1)
        self.pages = [
            ResourcePage(("knowledge_sources",),
                "统一本地资料与远程知识库，支持索引更新和搜索预览。PDK 器件资格、CDF 参数规则及"
                "已发布工艺数据继续以结构化接口为准。",
                [("添加本地资料", "knowledge_local", True),
                 ("添加远程知识库", "knowledge_remote", True)]),
            ResourcePage(("plugins", "mcp_servers"),
                f"管理本地插件、STDIO 与 Streamable HTTP 服务。内置与派生 MCP（如 {SERVER_ID}）由会话管理。",
                [("添加本地插件", "plugins", True), ("添加独立 MCP", "mcp_servers", True)]),
            ResourcePage(("skill_roots", "capability_roots", "skill_overrides"),
                "管理 Codex 技能默认启停；显式使用技能通过输入框附件选择。"
                "Cadence SKILL 语言资料属于知识源。",
                [("添加技能目录", "skill_roots", True), ("添加能力目录", "capability_roots", True),
                 ("单技能设置", "skill_overrides", True)]),
        ]
        self.data_page = DataSourcePage()
        self.all_pages = [*self.pages, self.data_page]
        for page, title in zip(self.all_pages, CATEGORIES):
            self.stack.addWidget(page)
            self.categories.addItem(title)
        self.categories.setCurrentRow(0)
        self.categories.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.knowledge = QPushButton("索引与搜索预览…")
        self.pages[0].layout().addWidget(self.knowledge)
        self.message = label()
        outer.addWidget(self.message)
        conflict = QHBoxLayout()
        self.adopt = QPushButton("采用最新默认")
        self.rebase = QPushButton("核对后保留草稿")
        for button in (self.adopt, self.rebase):
            conflict.addWidget(button)
            button.hide()
        conflict.addStretch()
        outer.addLayout(conflict)
        actions = QHBoxLayout()
        self.dirty = label()
        actions.addWidget(self.dirty, 1)
        self.close = QPushButton("关闭")
        self.save = QPushButton("保存工程默认")
        self.save.setProperty("bottomAction", True)
        actions.addWidget(self.close)
        actions.addWidget(self.save)
        outer.addLayout(actions)

    def _session_bar(self):
        """Session-only status row: the fixed selection report and one re-check."""
        self.session_bar = QWidget()
        column = QVBoxLayout(self.session_bar)
        column.setContentsMargins(0, 0, 0, 0)
        self.session_name = label()
        column.addWidget(self.session_name)
        row = QHBoxLayout()
        self.session_status = label()
        row.addWidget(self.session_status, 1)
        self.recheck = QPushButton("重新检查会话资源")
        self.recheck.setToolTip("重新检查当前会话正在使用的运行资源；只读，不重新读取配置")
        row.addWidget(self.recheck)
        column.addLayout(row)
        self.session_bar.hide()
        return self.session_bar

    @staticmethod
    def _session_notes(session, snapshot, configuration, manifest, source_label, selected):
        if not manifest:
            return [session.get("message", "没有当前会话。")]
        notes = []
        if session.get("recorded"):
            notes.append("历史检查结果，未重新连接资源。")
        elif snapshot.get("busy"):
            notes.append("任务执行中 · 最近检查结果。")
        elif configuration.get("state") == "preview":
            notes.append("尚未启动：显示选择预览。")
        else:
            notes.append("当前会话的固定选择。")
        if snapshot.get("configured") and not snapshot.get("busy") and (
                snapshot.get("status") in {"pending", "not_started"}):
            notes.append("等待会话进程启动。")
        if snapshot.get("configured") is False:
            notes.append("尚未配置外部资源。")
        if snapshot.get("manifest"):
            notes.append("清单：" + str(snapshot["manifest"]))
        drift = configuration.get("drift", [])
        notes.extend("资源变化：" + str(row["id"]) + "（" + DRIFT.get(row["reason"], "待核对") + "）"
                     for row in drift[:4])
        if len(drift) > 4:
            notes.append("另有 " + str(len(drift) - 4) + " 项资源变化。")
        notes.extend(str(issue)[:256] for issue in snapshot.get("issues", [])[:3])
        effective = configuration.get("effective", {}).get("manifest")
        if effective and selected.get("manifest"):
            changes = difference(effective, selected["manifest"])
            notes.append(source_label + "与会话选择有 " + str(len(changes)) + " 项差异。")
        return notes

    def set_model(self, backend, provider=None):
        index = self.backend.findData(backend)
        self.backend.blockSignals(True)
        self.backend.setCurrentIndex(index if index >= 0 else 0)
        self.backend.blockSignals(False)
        choices = (("Responses", "responses"),) if backend == "codex" else (
            ("Anthropic", "anthropic"), ("OpenAI", "openai"))
        preferred = provider or self.provider.currentData()
        self.provider.blockSignals(True)
        self.provider.clear()
        for text, value in choices:
            self.provider.addItem(text, value)
        selected = self.provider.findData(preferred)
        self.provider.setCurrentIndex(selected if selected >= 0 else 0)
        self.provider.blockSignals(False)

    def render(self, response, draft, *, busy, current, can_recheck=False,
               session_label="", data_draft=None, data_blocked=False):
        mode = self.mode.currentData()
        project = response.get("project_defaults", {})
        selected = response.get("selected_defaults", {})
        session = response.get("session") or {}
        snapshot = session.get("snapshot") or {}
        configuration = snapshot.get("configuration", {})
        doc = response.get(mode) or {}
        source_label = ("会话来源默认" if response.get("defaults_scope") == "session"
                        else "服务来源默认")
        self.mode.setItemText(1, source_label + " · 只读")
        if mode == "session":
            doc = configuration.get("effective", {})
        manifest = draft.manifest if mode == "project_defaults" else doc.get("manifest")
        editable = (mode == "project_defaults" and project.get("status") == "available"
                    and project.get("editable", False) and not busy and current)
        self.backend.setEnabled(editable)
        self.provider.setEnabled(editable)
        self.apply_model.setEnabled(editable)
        provenance = {"project": "工程默认", "provider": "Provider 指定的外部清单",
                      "environment": "环境指定的外部清单", "legacy": "历史选择"}
        self.source.setText((provenance.get(doc.get("source"), "") + "  " + doc.get("path", "")).strip())
        if not manifest or doc.get("status") == "unavailable":
            self.source.setText(doc.get("message", "当前没有可显示的配置"))
        self.source.setCursorPosition(0)
        for page in self.pages:
            page.render(manifest or {}, editable=editable,
                        snapshot=snapshot if mode == "session" else {})
        data_sources = response.get("data_sources") or {}
        data_editable = (mode == "project_defaults"
                         and data_sources.get("status") == "available"
                         and data_sources.get("editable", False)
                         and not busy and current)
        self.data_page.render(data_sources, editable=data_editable,
                              draft=data_draft, mode=mode, blocked=data_blocked)
        session_mode = mode == "session"
        self.session_bar.setVisible(session_mode)
        if session_mode:
            self.session_name.setText(session_label or "当前没有会话。")
            notes = self._session_notes(session, snapshot, configuration, manifest, source_label,
                                        selected)
            self.session_status.setText("\n".join(notes))
            self.session_status.setVisible(bool(notes))
        self.recheck.setEnabled(session_mode and can_recheck and not busy and current)
        messages = []
        if response.get("defaults_scope") == "service":
            messages.append("服务来源默认按工程服务的环境解析；启动窗口的额外参数可能不同，"
                            "实际选择请在进入会话后查看。")
        if selected.get("status") == "unavailable":
            messages.append("当前来源默认无法读取；工程默认可以单独管理。")
        elif selected.get("source") and selected["source"] != "project":
            messages.append("外部清单覆盖工程默认；保存工程默认不会修改外部来源。")
        effective = configuration.get("effective", {}).get("manifest")
        comparable = bool(draft.latest or effective and selected.get("manifest"))
        self.compare.setVisible(comparable)
        self.compare.setEnabled(comparable and not busy)
        self.reload.setEnabled(not busy and current)
        self.check.setEnabled(bool(manifest) and not busy and current)
        self.knowledge.setEnabled(bool(manifest and manifest.get("knowledge_sources"))
                                  and not busy and current)
        self.save.setEnabled(editable and draft.dirty and not draft.blocked)
        self.dirty.setText("有未保存的修改" if draft.dirty else "未修改")
        self.adopt.setVisible(draft.latest is not None)
        self.rebase.setVisible(draft.latest is not None)
        self.adopt.setEnabled(not busy and current)
        self.rebase.setEnabled(not busy and current)
        if draft.latest is not None and draft.manifest is not None:
            changes = difference(draft.latest["manifest"], draft.manifest)
            names = [KINDS[row["kind"]] + " / " + row["id"] for row in changes]
            messages.append("草稿与最新默认不同的条目：" + ("、".join(names[:4]) or "无") +
                            ("等 " + str(len(names)) + " 项" if len(names) > 4 else ""))
        self.summary.setText("\n".join(messages))
        self.summary.setVisible(bool(messages))
