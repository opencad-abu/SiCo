"""Composition and lifecycle of the LSF monitor window."""

from __future__ import annotations

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QCloseEvent
from PyQt5.QtWidgets import QTabWidget, QVBoxLayout, QWidget
from cadgui.branding import logo_text
from cadgui.chrome import SiMainWindow
from ..cache import MonitorTopologyCache
from ..collector import CollectorConfig
from ..model import ClusterSnapshot
from .workers import JobActionController, RefreshController
from .resources_panel import ResourcesPanel
from .jobs_panel import JobsPanel, job_collection_error
from .monitor_status import MonitorStatus
from .job_confirmation import confirm_job_kill
from .selection_state import SelectionState, selection_state
from .selection_validation import SelectionValidationController
from .selector import SelectorActions


class LsfLoadMonitorWindow(SiMainWindow):
    def __init__(
        self,
        *,
        config: CollectorConfig | None = None,
        initial_queue: str | None = None,
        refresh_interval: int = 10,
        controller: RefreshController | None = None,
        job_action_controller: JobActionController | None = None,
        validation_controller=None,
        auto_start: bool = True,
        output=None,
        topology_cache: MonitorTopologyCache | None = None,
        parent=None,
    ) -> None:
        super().__init__(f"{logo_text()}::LSF Loading Monitor", parent)
        # 监控窗口没有菜单栏：标题栏下面直接是内容区。
        self.menu_bar.hide()
        self.setObjectName("lsfLoadMonitorWindow")
        self.resize(1180, 720)
        self.setMinimumSize(1140, 480)
        # Startup preference only; after the first snapshot Qt owns selection.
        self._initial_queue = initial_queue
        self._snapshot: ClusterSnapshot | None = None
        self._closing = False
        resolved_config = config or CollectorConfig()
        self.resources = ResourcesPanel(refresh_interval, self)
        self.jobs = JobsPanel(self)
        self.status = MonitorStatus(self)
        self.setStatusBar(self.status)
        validation = validation_controller
        if output is not None and validation is None:
            validation = SelectionValidationController(resolved_config, self)
        self.selector = SelectorActions(
            output,
            read_selection=self._selection_state,
            show_status=self.status.show_status,
            close=self.close,
            validation_controller=validation,
        )
        self.controller = controller or RefreshController(
            resolved_config, self, topology_cache=topology_cache
        )
        self.controller.busyChanged.connect(self._set_busy)
        if hasattr(self.controller, "cachedSnapshotReady"):
            self.controller.cachedSnapshotReady.connect(self._apply_cached_snapshot)
        self.controller.snapshotReady.connect(self._apply_snapshot)
        self.controller.failed.connect(self._refresh_failed)
        self.job_actions = job_action_controller or JobActionController(
            resolved_config, self
        )
        self.job_actions.busyChanged.connect(self._set_job_action_busy)
        self.job_actions.succeeded.connect(self._job_kill_succeeded)
        self.job_actions.failed.connect(self._job_kill_failed)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._auto_refresh_timeout)

        self._build_ui()
        self._connect_ui()
        self.status.show_status("stale", "Waiting for first sample")
        self._sync_job_actions()
        self._sync_timer()
        if auto_start:
            QTimer.singleShot(0, self.refresh)

    def _build_ui(self) -> None:
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(7)
        self.tabs = QTabWidget(central)
        self.tabs.setObjectName("monitorTabs")
        self.selector.add_to_layout(self.resources.layout(), self.resources)
        self.tabs.addTab(self.resources, "Resources")
        self.tabs.addTab(self.jobs, "Jobs")
        outer.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

    def _connect_ui(self) -> None:
        resources = self.resources
        resources.refresh_button.clicked.connect(self.refresh)
        resources.reload_topology_button.clicked.connect(self.reload_topology)
        resources.auto_refresh.toggled.connect(self._sync_timer)
        resources.interval_spin.valueChanged.connect(self._sync_timer)
        resources.queue_combo.currentTextChanged.connect(self._queue_changed)
        resources.host_table.selectionModel().selectionChanged.connect(
            self.selector.sync
        )
        self.jobs.job_action_delegate.killRequested.connect(self._confirm_kill_job)
        for signal in (
            self.jobs.job_proxy.rowsInserted,
            self.jobs.job_proxy.rowsRemoved,
            self.jobs.job_proxy.modelReset,
        ):
            signal.connect(self._update_job_count)
        self.selector.connect()

    def _selection_state(self) -> SelectionState:
        return selection_state(
            self._snapshot,
            queue=self.resources.selected_queue(),
            host=self.resources.selected_host(),
            busy=self.controller.busy,
            status=self.status.kind,
            closing=self._closing,
        )

    def _update_job_count(self, *_args) -> None:
        self.jobs.update_count(self._snapshot)

    def _sync_timer(self, *_args) -> None:
        self.refresh_timer.setInterval(self.resources.interval_spin.value() * 1000)
        if self.resources.auto_refresh.isChecked() and not self._closing:
            self.refresh_timer.start()
        else:
            self.refresh_timer.stop()

    def _queue_changed(self, queue: str) -> None:
        if queue:
            self.refresh()

    def _auto_refresh_timeout(self) -> None:
        if not self.controller.busy:
            self.refresh()

    def refresh(self, _checked: bool = False) -> None:
        if self._closing:
            return
        queue = self.resources.selected_queue() or self._initial_queue
        self.controller.submit(queue or None)

    def reload_topology(self, _checked: bool = False) -> None:
        if self._closing:
            return
        queue = self.resources.selected_queue() or self._initial_queue
        self.controller.submit(queue or None, force_topology=True)

    def _set_busy(self, busy: bool) -> None:
        self.resources.set_busy(busy, self._closing)
        self._sync_job_actions()
        self.selector.sync()
        if busy:
            self.status.show_status("refreshing", "Refreshing LSF data")

    def _apply_snapshot(self, result: object) -> None:
        self._apply_snapshot_data(result, final=True)

    def _apply_cached_snapshot(self, result: object) -> None:
        self._apply_snapshot_data(result, final=False)

    def _apply_snapshot_data(self, result: object, *, final: bool) -> None:
        if self._closing or not isinstance(result, ClusterSnapshot):
            return
        self._snapshot = result
        preferred_queue = self.resources.selected_queue() or self._initial_queue
        self.resources.apply_snapshot(result, preferred_queue)
        self._initial_queue = None
        self.jobs.apply_snapshot(result)
        diagnostics = "; ".join(item.message for item in result.diagnostics)
        if not final:
            self.status.show_status("refreshing", "Refreshing LSF topology")
        elif result.status == "ready":
            self.status.show_status("ready", "Ready", diagnostics)
        elif result.status == "partial":
            self.status.show_status("partial", "Partial data", diagnostics)
        else:
            self.status.show_status("error", "Collection error", diagnostics)
        self._sync_job_actions()
        self.selector.sync()

        selected = self.resources.selected_queue()
        if final and result.queues and result.selected_queue is None and selected:
            QTimer.singleShot(0, self.refresh)

    def _refresh_failed(self, message: str) -> None:
        if self._closing:
            return
        if self._snapshot is None:
            self.status.show_status("error", "Collection error", message)
        else:
            self.status.show_status("stale", "Stale data", message)
        self._sync_job_actions()
        self.selector.sync()

    def _job_actions_available(self) -> bool:
        return (
            not self._closing
            and not self.controller.busy
            and not self.job_actions.busy
            and not job_collection_error(self._snapshot)
            and self._snapshot is not None
            and self.status.kind in {"ready", "partial"}
        )

    def _sync_job_actions(self, *_args) -> None:
        self.jobs.job_action_delegate.set_enabled(self._job_actions_available())

    def _confirm_kill_job(self, job_id: str) -> None:
        if not self._job_actions_available() or self._snapshot is None:
            return
        job = next(
            (item for item in self._snapshot.jobs if item.job_id == job_id),
            None,
        )
        if job is None or job.user != self._snapshot.user:
            self.status.show_status(
                "stale", "Job list needs refresh", f"Job {job_id} is no longer current"
            )
            self._sync_job_actions()
            return
        if confirm_job_kill(job, self):
            self.job_actions.kill_job(job.job_id)

    def _set_job_action_busy(self, busy: bool) -> None:
        self._sync_job_actions()
        if busy:
            self.status.show_status("refreshing", "Killing LSF job")

    def _job_kill_succeeded(self, job_id: str) -> None:
        if self._closing:
            return
        self.status.show_status("ready", f"Kill sent for job {job_id}")
        self.refresh()

    def _job_kill_failed(self, job_id: str, message: str) -> None:
        if self._closing:
            return
        self.status.show_status("error", f"Cannot kill job {job_id}", message)
        self._sync_job_actions()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self.refresh_timer.stop()
        self.resources.shutdown()
        self.jobs.shutdown()
        self.selector.shutdown()
        self.job_actions.shutdown()
        self.controller.shutdown()
        super().closeEvent(event)


__all__ = ["LsfLoadMonitorWindow"]
