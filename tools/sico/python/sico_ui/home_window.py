"""Independent project home: service status, platform entry and retained history."""

from types import SimpleNamespace

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QHeaderView, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from sico import PRODUCT_NAME
from .close_choice import CloseChoice
from .info_app import InfoApp
from .platform_menu import PlatformMenu, UNCONNECTED
from .receipts import DataReceipt
from .session_end_group import SessionEndGroup
from .si_prompt import SiConfirm
from .workspace import CopilotWorkspace


class HomeWindow(CopilotWorkspace):
    closed = pyqtSignal()

    def __init__(self, project, settings_path, opened, *, args=None):
        super().__init__(settings_path)
        self.api = None
        self.lifecycle = SimpleNamespace(closing=False)
        self.opened = opened
        self.receipt = DataReceipt(self)
        self.info_app = InfoApp(self, closing=lambda: self.lifecycle.closing)
        self.ending = SessionEndGroup(self)
        self.stop_receipt = DataReceipt(self)
        self.close_choice = CloseChoice(self, opened=lambda: (), end=self.ending.end,
                                       stop=self.stop_service, finished=self.finish)
        self.setWindowTitle(PRODUCT_NAME)
        content = QWidget()
        layout = QVBoxLayout(content)
        title = QLabel("SiCo")
        font = title.font()
        font.setPointSize(22)
        title.setFont(font)
        layout.addWidget(title)
        project_label = QLabel("工程：" + str(project))
        project_label.setTextFormat(Qt.PlainText)
        project_label.setWordWrap(True)
        layout.addWidget(project_label)
        self.hint = QLabel(UNCONNECTED)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.connect_button = QPushButton("连接 CDNS-IC…")
        self.connect_button.setEnabled(False)
        layout.addWidget(self.connect_button, 0, Qt.AlignLeft)
        layout.addWidget(QLabel("会话历史（双击浏览）"))
        self.history = QTreeWidget()
        self.history.setHeaderLabels(["会话", "状态"])
        self.history.setRootIsDecorated(False)
        self.history.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.history.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        layout.addWidget(self.history, 1)
        self.tabs.addTab(content, "首页")
        self.history.itemActivated.connect(self.browse)
        self.service_label = QLabel("正在准备项目服务…")
        self.add_service_status(self.service_label)
        self.retry_action = self.service_action("重试连接服务", lambda: self.retry())
        self.retry_action.setEnabled(False)
        self.stop_action = self.service_action("停止", self.request_stop)
        self.stop_action.setEnabled(False)
        self.platform_menu = PlatformMenu(self, opened, args=args)
        self.connect_button.clicked.connect(lambda _checked=False: self.platform_menu.connect())
        self.session_tail_action("关闭SiCo", self.close, "Ctrl+W")
        self.retry = lambda: None
        self._version = -1
        self._dialog = None
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.restore_layout()

    def ready(self, api):
        self.api = api
        self._version = -1
        self.connect_button.setEnabled(True)
        self.stop_action.setEnabled(True)
        self.retry_action.setEnabled(False)
        self.service_label.setText("项目服务已连接")
        self.platform_menu.refresh()
        self.refresh()

    def failed(self, error):
        self.service_label.setText("项目服务尚未就绪")
        self.retry_action.setEnabled(True)
        self.info_app.warning("项目服务启动未完成", str(error))

    def refresh(self):
        if self.api is None or self.lifecycle.closing or not self.isVisible():
            return
        connected = self.api.connection_state == "connected"
        self.service_label.setText("项目服务已连接" if connected else "项目服务连接已断开")
        self.retry_action.setEnabled(not connected)
        catalog = self.api.catalog_snapshot(self._version)
        if catalog is None:
            return
        selected = self.history.currentItem()
        selected_id = selected.data(0, Qt.UserRole) if selected else None
        self._version = catalog["version"]
        self.history.clear()
        for row in catalog["rows"]:
            item = QTreeWidgetItem([row.get("name") or row.get("title") or row["id"],
                                    str(row.get("status", ""))])
            item.setData(0, Qt.UserRole, row["id"])
            self.history.addTopLevelItem(item)
            if row["id"] == selected_id:
                self.history.setCurrentItem(item)

    def browse(self, item, _column=0):
        if self.api is not None and self.receipt.pending is None:
            self.receipt.watch(self.api.recovery.open_history(item.data(0, Qt.UserRole)),
                self.opened, lambda error: self.info_app.warning("历史会话未打开", str(error)))

    def request_stop(self):
        if self.api is None:
            return
        self.close_choice.stop_everything()

    def stop_service(self, settled, failed):
        try:
            self.stop_receipt.watch(self.api.stop_service(), settled, failed)
        except (ValueError, RuntimeError, OSError) as exc:
            failed(exc)

    def finish(self):
        self.lifecycle.closing = True
        self.close()

    def closeEvent(self, event):
        if self.api is not None and not self.lifecycle.closing:
            event.ignore()
            if self._dialog is not None:
                self._dialog.raise_()
                return
            dialog = self._dialog = SiConfirm("关闭 SiCo",
                "关闭窗口后项目服务按空闲策略继续运行；结束服务需先完成工程中的活动工作。", self)
            dialog.add_choice("service", "结束服务")
            dialog.add_choice("window", "关闭窗口", default=True)
            dialog.add_choice("cancel", "取消", escape=True)
            def chosen(key):
                self._dialog = None
                dialog.deleteLater()
                if key == "window":
                    self.finish()
                elif key == "service":
                    self.request_stop()
            dialog.chosen.connect(chosen)
            dialog.open()
            return
        self.lifecycle.closing = True
        self.timer.stop()
        self.close_choice.blocked.close()
        self.stop_receipt.clear()
        self.ending.close()
        self.platform_menu.close()
        self.receipt.clear()
        self.info_app.close()
        if self.isVisible() and self.api is not None:
            try:
                self.save_layout()
            except OSError:
                pass
        event.accept()
        self.closed.emit()
