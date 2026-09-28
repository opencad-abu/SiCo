"""Current workbench pages; all filtering and association work runs off Qt."""

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QComboBox, QVBoxLayout, QWidget

from sico.service.workbench_query import WorkbenchQuery

from .processes import ProcessPanel
from .receipts import DataReceipt
from .records import AuditPanel
from .workbench_list import ObjectList
from .workspace import EvenTabPanel


class Centers(QWidget):
    objectRequested = pyqtSignal(str, str)
    answerRequested = pyqtSignal(str, object)
    indexChanged = pyqtSignal(object)
    SNAPSHOT_IGNORED = frozenset({"model.delta", "model.status", "router.status", "token.usage"})

    def __init__(self, journal, data_service):
        super().__init__()
        self.setObjectName("copilotCenters")
        self.data_service = data_service
        self.source = data_service.source(journal)
        self.index = self.source.empty_query()
        self.live_session_id = journal.session_id
        self._snapshot_receipt = DataReceipt(self)
        self._snapshot_version = 0
        self._snapshot_needed = True
        self._snapshot_deferred = False
        self._suspended = False
        self._query_activation = self._query_serial = 0
        self._requested_audit = ""
        self.work_filter, self.stage_filter = QComboBox(), QComboBox()
        self.task_filter = QComboBox()
        self.work_filter.setObjectName("workFilter")
        self.stage_filter.setObjectName("stageFilter")
        self.task_filter.setObjectName("taskFilter")
        self.work_filter.setToolTip("按工程任务筛选：一个工程任务可跨多个阶段和多轮执行")
        self.stage_filter.setToolTip("按工程阶段筛选：选项跟随所选工程任务")
        self.task_filter.setToolTip("按单次执行筛选：一轮任务（不含启动时的上下文读取）")
        for combo in (self.work_filter, self.stage_filter, self.task_filter):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(10)
        self.work_filter.addItem("全部工程", "")
        self.stage_filter.addItem("全部阶段", "")
        self.task_filter.addItem("全部执行", "")
        self.work_filter.currentIndexChanged.connect(self.work_changed)
        self.stage_filter.currentIndexChanged.connect(self.stage_changed)
        self.task_filter.currentIndexChanged.connect(self.render)
        # 标签栏固定在顶部，筛选行放在标签下面，页面占满剩余空间。
        self.tabs = EvenTabPanel((self.work_filter, self.stage_filter, self.task_filter))
        self.reports, self.data = ObjectList("report"), ObjectList("data")
        self.audit = AuditPanel("暂无交互记录")
        self.processes = ProcessPanel(self.source)
        self.processes.activated.connect(self.objectRequested)
        self.audit.answerRequested.connect(self.answerRequested)
        for widget, label in ((self.audit, "审阅"), (self.reports, "汇报"), (self.data, "数据")):
            self.tabs.addTab(widget, label)
        self.tabs.addTab(self.processes, "进程")
        self.tabs.currentChanged.connect(self.tab_changed)
        for listing in (self.reports, self.data):
            listing.activated.connect(self.objectRequested)
            listing.queryChanged.connect(self.query_changed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs, 1)
        self.scroll_views = [self.reports.list, self.data.list, self.audit.list, self.audit.detail,
                             self.audit.editor_scroll, self.processes.list]
        self.destroyed.connect(lambda *_: self._dispose())
        self.flush()

    def tabText(self, index):
        return self.tabs.tabText(index)

    def _invalidate(self):
        self._query_activation += 1
        self._query_serial += 1
        self._suspended = True
        self.processes.invalidate()

    def _dispose(self):
        self._invalidate()
        self.source.close()

    def closeEvent(self, event):
        self.suspend()
        self.source.close()
        super().closeEvent(event)

    def suspend(self):
        self._invalidate()
        self.processes.suspend()
        self._snapshot_receipt.clear()
        for listing in (self.reports, self.data):
            listing.set_pending(True)

    def resume(self, activation):
        self._query_activation = activation
        self._suspended = False
        self.processes.bind(self.source)
        self.query_changed()

    def reset(self, source, *, activation=None):
        self.suspend()
        self.source.close()
        self.source = source
        self.processes.bind(self.source)
        self.index = self.source.empty_query()
        self._snapshot_version = 0
        self._snapshot_needed = True
        self._suspended = False
        if activation is not None:
            self._query_activation = activation
        self._requested_audit = ""
        self.audit.clear()
        self.audit.read_only = source.session_id != self.live_session_id
        for listing in (self.reports, self.data):
            listing.reset()
        self._combo(self.work_filter, (("", "全部工程"),), "")
        self._combo(self.stage_filter, (("", "全部阶段"),), "")
        self._combo(self.task_filter, (("", "全部执行"),), "")
        self.indexChanged.emit(self.index)
        self.flush()

    def receive(self, event):
        if event["kind"] not in self.SNAPSHOT_IGNORED:
            self._snapshot_needed = True

    def defer_snapshot(self):
        self._snapshot_deferred = True

    def release_snapshot(self):
        self._snapshot_deferred = False

    def query_changed(self):
        self._query_serial += 1
        self._snapshot_needed = True
        for listing in (self.reports, self.data):
            listing.set_pending(True)
        self.request_snapshot()

    def request_snapshot(self):
        if (self._suspended or self._snapshot_deferred or not self._snapshot_needed
                or self._snapshot_receipt.pending):
            return
        self._query_serial += 1
        source, activation, serial = self.source, self._query_activation, self._query_serial
        query = WorkbenchQuery(
            activation=activation, query_id=str(serial),
            work_id=self.work_filter.currentData() or "",
            stage_id=self.stage_filter.currentData() or "",
            task_id=self.task_filter.currentData() or "",
            data=self.data.query(), report=self.reports.query(), audit_id=self._requested_audit,
        )
        self._snapshot_needed = False
        for listing in (self.reports, self.data):
            listing.set_pending(True)

        def current():
            if (self._suspended or source is not self.source
                    or activation != self._query_activation or serial != self._query_serial):
                return False
            if source.scope is not None:
                try:
                    source.scope.validate_current()
                except ValueError:
                    return False
            return True

        def completed(result):
            if current():
                source.validate_current()
                version, snapshot = result
                if (snapshot.contract != "copilot.workbench.pages.v1"
                        or snapshot.source is not source or snapshot.identity != source.identity
                        or snapshot.session_id != source.session_id
                        or snapshot.query.activation != activation
                        or snapshot.query.query_id != query.query_id
                        or version != snapshot.version or version < self._snapshot_version):
                    raise ValueError("Workbench query identity/version mismatch")
                for kind in ("data", "report"):
                    page = snapshot.pages[kind]
                    if (page["contract"] != "copilot.workbench.query.v1"
                            or page["kind"] != kind or page["session_id"] != source.session_id
                            or page["version"] != version or page["query_id"] != query.query_id
                            or page["activation"] != activation
                            or page["page_size"] != getattr(query, kind).page_size
                            or page["offset"] < 0 or page["total"] < len(page["rows"])
                            or len(page["rows"]) > getattr(query, kind).page_size):
                        raise ValueError("Workbench page contract mismatch")
                self._apply_snapshot(result)
            self.flush()

        def failed(exc):
            if current():
                self._snapshot_failed(exc)
            else:
                self.flush()

        try:
            future = source.query_pages(query)
            self._snapshot_receipt.watch(future, completed, failed)
        except (ValueError, RuntimeError) as exc:
            failed(exc)

    def _snapshot_failed(self, _exc):
        self.audit.detail.setPlainText("工作台数据暂不可读，请稍后重试。")
        for listing in (self.reports, self.data):
            listing.failed()

    def _apply_snapshot(self, result):
        self._snapshot_version, snapshot = result
        self.index = snapshot
        self._combo(self.work_filter, (("", "全部工程"),) + tuple(
            (key, row["title"]) for key, row in snapshot.works.items()), snapshot.query.work_id)
        self._combo(self.stage_filter, (("", "全部阶段"),) + tuple(
            (key, row["stage_title"]) for key, row in snapshot.stages.items()),
            snapshot.query.stage_id)
        self._combo(self.task_filter, (("", "全部执行"),) + tuple(
            (key, row["title"]) for key, row in snapshot.tasks.items()), snapshot.query.task_id)
        self.reports.apply_page(snapshot.pages["report"])
        self.data.apply_page(snapshot.pages["data"])
        self.audit.read_only = snapshot.session_id != self.live_session_id
        self.audit.sync_records(snapshot.records, snapshot.audits)
        if self._requested_audit in snapshot.audits:
            self.audit.render_audit(self._requested_audit)
            self._requested_audit = ""
        self.indexChanged.emit(snapshot)

    @staticmethod
    def _combo(combo, options, chosen):
        old = tuple((combo.itemData(i), combo.itemText(i)) for i in range(combo.count()))
        combo.blockSignals(True)
        if old != options:
            combo.clear()
            for key, label in options:
                combo.addItem(label, key)
        combo.setCurrentIndex(max(0, combo.findData(chosen)))
        combo.blockSignals(False)

    def flush(self):
        self.request_snapshot()

    def work_changed(self, *args):
        self._combo(self.stage_filter, (("", "全部阶段"),), "")
        self.stage_changed()

    def stage_changed(self, *args):
        self._combo(self.task_filter, (("", "全部执行"),), "")
        self.render()

    def render(self, *args):
        for listing in (self.reports, self.data):
            listing.offset = 0
            listing._selected_key = listing._target_key = ""
        self.query_changed()

    def show_tools(self):
        self.tabs.setCurrentWidget(self.data)

    def tab_changed(self, _index):
        filters = self.tabs.currentWidget() is not self.processes
        self.work_filter.setVisible(filters)
        self.stage_filter.setVisible(filters)
        self.task_filter.setVisible(filters)

    def show_data(self, key):
        self.reveal("data", key)
        return key

    def reveal(self, kind, key):
        if kind == "process":
            self.tabs.setCurrentWidget(self.processes)
            self.processes.reveal(key)
            return
        listing = self.reports if kind == "report" else self.data
        self.tabs.setCurrentWidget(listing)
        if listing.select_key(key):
            return
        for combo in (self.work_filter, self.stage_filter, self.task_filter):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        listing.reveal(key)

    def show_audit(self, audit_id):
        self._requested_audit = audit_id
        for combo in (self.work_filter, self.stage_filter, self.task_filter):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.tabs.setCurrentWidget(self.audit)
        self.query_changed()
