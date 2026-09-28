"""Platform connection actions shared by the independent home and session pages."""

from types import SimpleNamespace

from PyQt5.QtCore import QObject, Qt, QTimer
from PyQt5.QtWidgets import QLabel, QMenu

from sico.service.platform_startup import PlatformStartup
from .credentials import CredentialCancelled, credential_environment
from .platform_picker import PlatformPicker
from .receipts import DataReceipt
from .si_prompt import SiConfirm

UNCONNECTED = "尚未连接 IC 平台，请通过“通信”菜单选择并连接平台。"


class PlatformMenu(QObject):
    def __init__(self, window, opened, *, args=None):
        super().__init__(window)
        self.window, self.opened, self.args = window, opened, args
        self.picker = self.startup = self.confirmation = None
        self.execution = ""
        self.ready = DataReceipt(self)
        self.credentials = DataReceipt(self)
        self.health = DataReceipt(self)
        self.menu = QMenu("通信", window)
        window.menuBar().insertMenu(window.view_menu.menuAction(), self.menu)
        self.connect_action = self.menu.addAction("连接 CDNS-IC…", lambda _checked=False: self.connect())
        for platform in ("EMPY-AE", "SNPS-CC"):
            action = self.menu.addAction("连接 " + platform + "…（暂未支持）")
            action.setEnabled(False)
            action.setToolTip("当前版本尚未实现此平台的通信后台")
        self.cancel_action = self.menu.addAction("取消连接", lambda _checked=False: self.cancel())
        self.cancel_action.setVisible(False)
        self.menu.addSeparator()
        self.disconnect_action = self.menu.addAction("断开连接", lambda _checked=False: self.disconnect())
        self.menu.addAction("连接信息…", lambda _checked=False: self.information())
        self.label = QLabel(UNCONNECTED)
        self.label.setObjectName("platformStatus")
        self.label.setTextFormat(Qt.PlainText)
        self.label.setToolTip("通过“通信 → 连接信息…”查看完整连接信息。")
        if "conversation" in window.pages:
            window.add_platform_status(self.label)
        else:
            self.label.setParent(window)
            self.label.hide()
        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()
        window.destroyed.connect(lambda *_args: self.close())

    def token(self):
        page = getattr(self.window, "page", None)
        if page is None or page.reviewing is not None:
            return None
        token = page.session
        api = self.window.api
        return token if token.runtime_id is not None and not api.is_closing(token) else None

    def refresh(self):
        window = self.window
        if window.lifecycle.closing:
            self.close()
            return
        api = window.api
        token = self.token() if api else None
        self.connect_action.setEnabled(api is not None and self.startup is None)
        self.cancel_action.setVisible(self.startup is not None)
        self.disconnect_action.setEnabled(token is not None and self.startup is None)
        if self.startup is not None:
            self.label.setText("正在关联 CDNS-IC…")
        elif token is None:
            self.label.setText(UNCONNECTED)
        elif self.health.pending is None:
            context = api.view(token).context
            if not self.label.text().startswith("CDNS-IC"):
                self.label.setText("正在核对 CDNS-IC 连接…")
            def observed(rows):
                if self.token() != token or window.lifecycle.closing:
                    return
                selected = next((row for row in rows if (row["instance"], row["generation"]) ==
                           (context.instance_id, context.generation)), None)
                live = selected is not None
                self.execution = ("执行主机：" + selected["node"] + "\nLSF 作业：" +
                                  (selected["job"] or "非 LSF") if live else "执行主机未确认")
                self.label.setText(("CDNS-IC · 已连接 · " if live else
                    "CDNS-IC · 连接不可用 · ") + context.instance_id)
            try:
                self.health.watch(api.platforms.instances(), observed,
                    lambda _error: self.label.setText("CDNS-IC 连接状态未确认"))
            except (ValueError, RuntimeError):
                self.label.setText("CDNS-IC 连接状态未确认")

    def connect(self):
        if self.startup is not None or self.window.api is None:
            return
        if self.token() is not None:
            self.window.info_app.notice("已有 IC 平台关联",
                "请先通过“通信 → 断开连接”结束当前会话的关联，再选择其他实例。其他会话不会切换目标。")
            return
        if self.picker is not None:
            self.picker.raise_()
            return
        picker = self.picker = PlatformPicker(self.window, self.window.api.platforms)
        picker.selected.connect(self._start)
        def finished(_result):
            self.picker = None
            picker.deleteLater()
        picker.finished.connect(finished)
        picker.open()

    def _start(self, candidate):
        args = self.args or SimpleNamespace(provider_config=None,
                                            launch_dir=str(self.window.api.launch_directory))
        startup = self.startup = PlatformStartup(self.window.api, candidate, args)
        self.credentials.watch(startup.configuration, self._credential, self._failed)
        self.ready.watch(startup.ready, self._opened, self._failed)
        self.refresh()

    def _credential(self, key):
        if key and self.startup is not None:
            try:
                environment = credential_environment(key)
                if self.startup is not None:
                    self.startup.provide_credential(environment[key])
            except CredentialCancelled:
                self._failed("未提供模型凭据；可再次选择平台连接")

    def cancel(self):
        self._finish_startup()
        self.window.info_app.notice("连接等待已取消",
            "主页面和项目服务保留。若会话已被服务接收，可在会话历史中查看；不会自动提交任务。")
        self.refresh()

    def _finish_startup(self):
        self.ready.clear()
        self.credentials.clear()
        if self.startup is not None:
            self.startup.close()
            self.startup = None

    def _opened(self, frontend):
        self._finish_startup()
        if not self.window.lifecycle.closing:
            self.opened(frontend)
            self.refresh()

    def _failed(self, error):
        self._finish_startup()
        if not self.window.lifecycle.closing:
            self.window.info_app.warning("平台关联未完成", str(error))
            self.refresh()

    def disconnect(self):
        token = self.token()
        if token is None or self.confirmation is not None:
            return
        dialog = self.confirmation = SiConfirm("断开 IC 平台连接",
            "结束并归档当前会话，解除它的 IC 平台关联？\n"
            "正在执行的任务会停止，已发出的操作可能仍需核对。\n"
            "Virtuoso、其他会话及项目服务继续运行，当前记录保留。", self.window)
        dialog.add_choice("disconnect", "结束会话并断开")
        dialog.add_choice("cancel", "取消", default=True, escape=True)
        def chosen(key):
            self.confirmation = None
            dialog.deleteLater()
            if key == "disconnect" and self.token() == token:
                self.window.service_connection.ending.end(token,
                    lambda _result: self.refresh(),
                    lambda error: self.window.info_app.warning("连接尚未断开", str(error)))
        dialog.chosen.connect(chosen)
        dialog.open()

    def information(self):
        token = self.token() if self.window.api else None
        text = UNCONNECTED
        if token is not None:
            context = self.window.api.view(token).context
            text = ("平台：CDNS-IC\n实例：" + context.instance_id +
                    "\n工程：" + str(context.snapshot.get("cwd", "")) +
                    "\n目标：" + context.target_id + "\n" + self.execution + "\n状态：" + self.label.text())
        self.window.info_app.info("IC 平台连接信息", text)

    def close(self):
        self.timer.stop()
        self.health.clear()
        self._finish_startup()
        if self.picker is not None:
            self.picker.reject()
        if self.confirmation is not None:
            self.confirmation.reject()
