"""Nonblocking IC instance selection; only public candidate details reach widgets."""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem

from .chrome import SiDialog
from .receipts import DataReceipt


class PlatformPicker(SiDialog):
    selected = pyqtSignal(str)

    def __init__(self, parent, platforms):
        super().__init__("连接 CDNS-IC", parent, ("close",))
        self.platforms = platforms
        self.receipt = DataReceipt(self)
        self.setWindowModality(Qt.WindowModal)
        self.message = QLabel("正在查询当前工程的 CDNS-IC 实例…")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
        self.body_layout.addWidget(self.message)
        self.instances = QTreeWidget()
        self.instances.setHeaderLabels(["实例", "版本", "执行主机", "LSF 作业", "工程目录"])
        self.instances.setRootIsDecorated(False)
        self.instances.setSelectionMode(QTreeWidget.SingleSelection)
        self.body_layout.addWidget(self.instances)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton("刷新")
        self.connect_button = QPushButton("连接")
        self.connect_button.setEnabled(False)
        cancel = QPushButton("取消")
        actions.addWidget(self.refresh_button)
        actions.addStretch(1)
        actions.addWidget(self.connect_button)
        actions.addWidget(cancel)
        self.body_layout.addLayout(actions)
        self.refresh_button.clicked.connect(lambda _checked=False: self.refresh())
        self.connect_button.clicked.connect(lambda _checked=False: self.choose())
        cancel.clicked.connect(self.reject)
        self.instances.itemSelectionChanged.connect(lambda: self.connect_button.setEnabled(
            bool(self.instances.selectedItems())))
        self.instances.itemDoubleClicked.connect(lambda *_args: self.choose())
        self.finished.connect(lambda _result: self.receipt.clear())
        self.resize(760, 360)
        self.refresh()

    def refresh(self):
        self.instances.clear()
        self.refresh_button.setEnabled(False)
        self.message.setText("正在查询当前工程的 CDNS-IC 实例…")
        try:
            self.receipt.watch(self.platforms.instances(), self._display, self._failed)
        except (ValueError, RuntimeError) as exc:
            self._failed(exc)

    def _display(self, rows):
        self.refresh_button.setEnabled(True)
        for row in rows:
            item = QTreeWidgetItem([row["instance"], row["version"], row.get("node", ""),
                                   row.get("job", ""), row["project"]])
            item.setData(0, Qt.UserRole, row["id"])
            self.instances.addTopLevelItem(item)
        for column in range(4):
            self.instances.resizeColumnToContents(column)
        self.message.setText("请选择要关联的 CDNS-IC 实例。" if rows else
            "未发现可连接实例。请在当前工程启动 Virtuoso，并加载站点的 SiCo 集成后刷新。"
            "跨节点还需同用户 SSH 免密登录、已验证主机密钥和共享工程路径；SiCo 不会自动启动 IC 平台。")

    def _failed(self, error):
        self.refresh_button.setEnabled(True)
        self.message.setText("实例查询未完成：" + str(error))

    def choose(self):
        items = self.instances.selectedItems()
        if items:
            self.selected.emit(items[0].data(0, Qt.UserRole))
            self.accept()
