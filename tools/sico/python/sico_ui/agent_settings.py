"""Asynchronous settings dialog bound to the page that opened it."""

from copy import deepcopy

from PyQt5.QtCore import QEvent, QTimer
from PyQt5.QtWidgets import QDialog
from cadai.optional_resources import DATA_SOURCE_ENV
from sico.service.published import thaw

from .agent_resource_editor import ResourceEditor
from .agent_settings_conflicts import SettingsConflictActions
from .agent_settings_dialogs import SettingsAuxiliaryDialogs
from .agent_settings_state import SettingsDraft
from .agent_settings_view import SettingsView
from .chrome import SiDialog
from .data_source_editor import DataSourceEditor
from .receipts import DataReceipt


class AgentSettingsDialog(SettingsConflictActions, SettingsAuxiliaryDialogs, SiDialog):
    def __init__(self, window):
        super().__init__("智能体设置", window)
        self.window, self.api = window, window.api
        self.page = getattr(window, "page", None)
        self.activation = self.page.activation if self.page else None
        self.reviewing = self.page.reviewing if self.page else None
        self.token = (self.reviewing or self.page.session) if self.page else None
        self.context = getattr(getattr(window, "presentation", None), "displayed_context", None)
        self.closed = False
        self.draft, self.response = SettingsDraft(), {}
        self.busy, self.stale = False, False
        self.saving, self.discard = False, False
        self.editor = self.confirmation = None
        self.model_saved = ("codex", "responses")
        self.model_revision = None
        self.data_saved = {}
        self.data_revision = None
        self.data_edits = {}
        self.data_saving = self.data_blocked = False
        self.receipt = DataReceipt(self)
        self.ui = SettingsView(self)
        self.content_layout().addWidget(self.ui)
        self.resize(900, 680)
        self.setMinimumSize(680, 520)
        self.ui.caption.setText("工程：" + str(self.api.launch_directory))
        self.ui.caption.setCursorPosition(0)
        self.ui.reload.clicked.connect(lambda _checked=False: self.reload())
        self.ui.save.clicked.connect(lambda _checked=False: self.save())
        self.ui.apply_model.clicked.connect(lambda _checked=False: self.save_model())
        self.ui.backend.currentIndexChanged.connect(lambda _index: self.render())
        self.ui.provider.currentIndexChanged.connect(lambda _index: self.render())
        self.ui.close.clicked.connect(lambda _checked=False: self.close())
        self.ui.mode.currentIndexChanged.connect(lambda _index: self.render())
        self.ui.adopt.clicked.connect(lambda _checked=False: self.choose_latest())
        self.ui.rebase.clicked.connect(lambda _checked=False: self.keep_draft())
        self.ui.compare.clicked.connect(lambda _checked=False: self.compare())
        self.ui.check.clicked.connect(lambda _checked=False: self.check_resources())
        self.ui.recheck.clicked.connect(lambda _checked=False: self.recheck_session())
        self.ui.knowledge.clicked.connect(lambda _checked=False: self.preview_knowledge())
        for page in self.ui.pages:
            page.addRequested.connect(lambda kind: self.edit(kind))
            page.editRequested.connect(self.edit)
            page.toggleRequested.connect(self.toggle)
            page.removeRequested.connect(self.remove)
        self.ui.data_page.editRequested.connect(self.edit_data_source)
        self.ui.data_page.clearRequested.connect(self.clear_data_source)
        self.ui.data_page.saveRequested.connect(self.save_data_sources)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_scope)
        self.timer.start(200)
        window.installEventFilter(self)
        self.finished.connect(lambda _result: self.cleanup())
        self.reload()

    def current(self):
        if self.closed or self.stale or self.window.lifecycle.closing or self.window.api is not self.api:
            return False
        if self.page and (self.page.activation != self.activation or
                          self.page.reviewing != self.reviewing or self.page.opening or
                          getattr(getattr(self.window, "presentation", None),
                                  "displayed_context", None) != self.context):
            return False
        try:
            return self.token is None or isinstance(self.token, str) or self.api.owns(self.token)
        except ValueError:
            return False

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() == QEvent.Hide:
            if self.window.lifecycle.closing:
                self.discard_close()
            elif self.page is None:
                self.invalidate()
        return super().eventFilter(watched, event)

    def check_scope(self):
        if not self.current() and not self.stale:
            self.invalidate()

    def invalidate(self):
        self.stale = True
        self.receipt.clear()
        if self.editor:
            self.editor.reject()
        if self.confirmation:
            self.confirmation.reject()
        self.ui.message.setText("页面或连接已切换，草稿保留在此窗口。请在当前页面重新打开设置；"
                                "已提交的保存需重新读取核对。")
        self.render()

    def render(self):
        self.ui.render(self.response, self.draft, busy=self.busy, current=self.current(),
                       can_recheck=self.can_recheck(), session_label=self.session_label(),
                       data_draft=self.data_edits, data_blocked=self.data_blocked)
        self.ui.apply_model.setEnabled(
            self.can_edit()
            and (self.ui.backend.currentData(), self.ui.provider.currentData())
            != self.model_saved
        )

    def session_label(self):
        """Where the read-only session view comes from: live worker or journal."""
        if self.token is None:
            return ""
        if isinstance(self.token, str):
            return "会话：历史记录 " + self.token + " · 只读"
        display = getattr(self.api, "display_name", None)
        if display is None:
            return "会话：当前会话 · 只读"
        try:
            return "会话：" + display(self.token) + " · 只读"
        except (ValueError, RuntimeError, OSError):
            return "会话：当前会话 · 只读"

    def can_recheck(self):
        """Only a captured live session with a readable resource snapshot may re-check."""
        session = self.response.get("session") or {}
        return bool(self.current() and self.token is not None
                    and not isinstance(self.token, str)
                    and session.get("status") == "available")

    def recheck_session(self):
        if self.busy or not self.can_recheck():
            return
        self.ui.message.setText("正在检查当前会话资源…")
        try:
            self.request(self.api.command(self.token, "resource_status"), self.session_rechecked)
        except (ValueError, RuntimeError, OSError) as exc:
            self.failed(exc)

    def session_rechecked(self, value):
        session = self.response.get("session")
        snapshot = session.get("snapshot") if isinstance(session, dict) else None
        if isinstance(snapshot, dict) and isinstance(value, dict):
            snapshot.update(value)
        self.ui.message.setText("已重新检查当前会话资源。")

    def request(self, future, success):
        self.busy = True
        self.render()
        def ready(value):
            self.busy = False
            if not self.current():
                self.check_scope()
                return
            success(thaw(value))
            self.render()
        self.receipt.watch(future, ready, self.failed)

    def reload(self):
        if not self.current() or self.busy:
            return
        self.ui.message.setText("正在读取配置…")
        try:
            self.request(self.api.agent_settings.read(self.token), self.loaded)
        except (ValueError, RuntimeError, OSError) as exc:
            self.failed(exc)

    def loaded(self, value):
        self.response = thaw(value)
        model_settings = value.get("model_settings") or {}
        self.model_saved = (
            model_settings.get("backend", "codex"),
            model_settings.get("provider", "responses"),
        )
        self.model_revision = model_settings.get("revision")
        self.ui.set_model(*self.model_saved)
        if model_settings.get("status") == "unavailable":
            self.ui.model_note.setText(
                model_settings.get("message", "模型设置不可读，暂按 Codex/Responses 处理。")
            )
        data_sources = value.get("data_sources") or {}
        self.data_saved = dict(data_sources.get("project") or {})
        self.data_revision = data_sources.get("revision")
        self.data_edits = {}
        self.data_saving = self.data_blocked = False
        doc = value["project_defaults"]
        if doc["status"] == "available":
            self.draft.load(doc)
            self.ui.message.setText("配置已读取。")
        else:
            self.ui.message.setText(doc["message"] + "；原有草稿保留。")

    def failed(self, error):
        self.busy = False
        if self.saving:
            self.draft.blocked = True
            self.saving = False
        if self.data_saving:
            self.data_blocked = True
            self.data_saving = False
        if not self.current():
            self.check_scope()
            return
        self.ui.message.setText(str(error) + "；草稿已保留。")
        self.render()

    def save(self):
        if not self.can_edit() or self.draft.blocked or not self.draft.dirty:
            return
        self.ui.message.setText("正在保存工程默认…")
        self.saving = True
        try:
            self.request(self.api.agent_settings.save(self.draft.manifest,
                self.draft.document["revision"], self.token), self.saved)
        except (ValueError, RuntimeError, OSError) as exc:
            self.draft.blocked = True
            self.failed(exc)

    def saved(self, value):
        self.saving = False
        if value["outcome"] == "saved":
            self.draft.accept(value["document"])
            self.response["project_defaults"] = {"status": "available", **value["document"]}
            self.ui.message.setText("工程默认已保存，将用于新会话。")
            QTimer.singleShot(0, self.reload)
        else:
            self.draft.blocked = value["outcome"] in {"conflict", "uncertain"}
            self.ui.message.setText(value["message"])

    def save_model(self):
        if not self.can_edit():
            return
        selected = (self.ui.backend.currentData(), self.ui.provider.currentData())
        if selected == self.model_saved:
            self.ui.message.setText("模型设置未变化。")
            return
        self.ui.message.setText("正在保存模型设置…")
        try:
            self.request(
                self.api.agent_settings.save_model(
                    selected[0], selected[1], self.model_revision, self.token
                ),
                self.model_result,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            self.failed(exc)

    def model_result(self, value):
        if value.get("outcome") == "saved":
            document = value["document"]
            self.model_saved = (
                document.get("backend", "codex"),
                document.get("provider", "responses"),
            )
            self.model_revision = document.get("revision")
            self.response["model_settings"] = document
            self.ui.message.setText("模型设置已保存，将用于新会话；已有会话保持原配置。")
        else:
            self.ui.message.setText(value.get("message", "模型设置保存未完成。"))
            if value.get("outcome") in {"conflict", "uncertain"}:
                QTimer.singleShot(0, self.reload)

    def can_edit_data(self):
        data = self.response.get("data_sources") or {}
        return (self.current() and not self.busy
                and self.ui.mode.currentData() == "project_defaults"
                and data.get("status") == "available"
                and data.get("editable", False))

    def data_values(self):
        values = dict(self.data_saved)
        values.update(self.data_edits)
        return {kind: values.get(kind) for kind in DATA_SOURCE_ENV}

    def edit_data_source(self, kind):
        if not self.can_edit_data() or self.editor is not None:
            return
        current = self.data_edits.get(kind, self.data_saved.get(kind))
        editor = self.editor = DataSourceEditor(self, kind, current or "")

        def finished(result):
            self.editor = None
            if result == QDialog.Accepted and self.can_edit_data():
                self.data_edits[kind] = editor.value
                self.data_blocked = False
                self.ui.message.setText("数据源路径已加入草稿，保存后用于新会话。")
                self.render()
            editor.deleteLater()

        editor.finished.connect(finished)
        editor.open()

    def clear_data_source(self, kind):
        if self.can_edit_data():
            self.data_edits[kind] = None
            self.data_blocked = False
            self.ui.message.setText("已清除工程覆盖；保存后恢复服务环境默认。")
            self.render()

    def save_data_sources(self):
        if not self.can_edit_data() or not self.data_edits or self.data_blocked:
            return
        values = self.data_values()
        self.ui.message.setText("正在保存数据源设置…")
        self.data_saving = True
        try:
            self.request(self.api.agent_settings.save_data_sources(
                values["pdk_data"], values["circuit_templates"],
                self.data_revision, self.token), self.data_result)
        except (ValueError, RuntimeError, OSError) as exc:
            self.data_saving = False
            self.failed(exc)

    def data_result(self, value):
        self.data_saving = False
        if value.get("outcome") == "saved":
            document = value["document"]
            self.response["data_sources"] = document
            self.data_saved = dict(document.get("project") or {})
            self.data_revision = document.get("revision")
            self.data_edits = {}
            self.data_blocked = False
            self.ui.message.setText("数据源设置已保存，将用于新会话。")
        else:
            self.data_blocked = value.get("outcome") in {"conflict", "uncertain"}
            self.ui.message.setText(value.get("message", "数据源设置保存未完成。"))

    def can_edit(self):
        return (self.current() and not self.busy and self.ui.mode.currentData() == "project_defaults"
                and self.response.get("project_defaults", {}).get("editable", False))

    def edit(self, kind, index=-1):
        if not self.can_edit() or self.editor is not None:
            return
        knowledge_type = "remote" if kind == "knowledge_remote" else "local"
        if kind in {"knowledge_local", "knowledge_remote"}:
            kind = "knowledge_sources"
        row = None if index < 0 else deepcopy(self.draft.manifest[kind][index])
        from .mcp_resource_editor import McpResourceEditor
        from .skill_override_editor import SkillOverrideEditor
        from .knowledge_editor import KnowledgeEditor

        editor = self.editor = (McpResourceEditor(self, row) if kind == "mcp_servers" else
                               SkillOverrideEditor(self, row) if kind == "skill_overrides" else
                               KnowledgeEditor(self, row, knowledge_type) if kind == "knowledge_sources" else
                               ResourceEditor(self, kind, row))
        def finished(result):
            self.editor = None
            if result == QDialog.Accepted and self.can_edit():
                try:
                    self.draft.replace(kind, index, editor.value)
                    self.ui.message.setText("已加入草稿，保存后用于新会话。")
                except (ValueError, RuntimeError, OSError) as exc:
                    self.ui.message.setText(str(exc))
                self.render()
            editor.deleteLater()
        editor.finished.connect(finished)
        editor.open()

    def toggle(self, kind, index):
        if self.can_edit():
            self.draft.toggle(kind, index)
            self.render()

    def remove(self, kind, index):
        if self.can_edit():
            self.draft.remove(kind, index)
            self.ui.message.setText("已从草稿移除；外部文件保留。")
            self.render()

    def reject(self):
        self.close()

    def closeEvent(self, event):
        if not self.discard and (self.draft.dirty or self.data_edits or self.busy):
            event.ignore()
            message = ("保存可能已提交；关闭后请重新读取核对结果。" if self.saving else
                       "关闭此设置窗口将放弃未保存的草稿。")
            self.confirm(message, self.discard_close, closing=True)
            return
        event.accept()
        QDialog.done(self, QDialog.Rejected)

    def cleanup(self):
        self.closed = True
        self.timer.stop()
        self.receipt.clear()
        self.window.removeEventFilter(self)
        for child in (self.editor, self.confirmation, getattr(self, "diff_dialog", None),
                      getattr(self, "check_dialog", None), getattr(self, "knowledge_dialog", None)):
            if child:
                child.reject()


def show_agent_settings(window):
    api = window.api
    if api is None or not hasattr(api, "agent_settings"):
        window.info_app.notice("智能体设置暂不可用", "请先连接支持设置管理的工程服务。")
        return
    previous = getattr(window, "agent_settings_dialog", None)
    if previous is not None and previous.isVisible() and previous.current():
        previous.raise_()
        return
    window.agent_settings_dialog = AgentSettingsDialog(window)
    window.agent_settings_dialog.show()
