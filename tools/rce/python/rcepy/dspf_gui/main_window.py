"""Main analysis workspace for indexed DSPF data."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
from sicoresources import icon as resource_icon
import time
from typing import Any

from PyQt5.QtCore import QSignalBlocker, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QCloseEvent, QIcon, QKeySequence
from cadgui.branding import logo_text
from cadgui.chrome import SiMainWindow
from cadgui.prompts import ask_file, notice
from PyQt5.QtWidgets import (
    QAction,
    QLabel,
    QMenu,
    QProgressBar,
    QStyle,
)

from .analysis_actions import AnalysisActionsMixin
from .models import create_models
from .oa_actions import OaBridgeMixin
from .window_ui import build_workspace
from .workers import IndexController


def _logo_path() -> Path | None:
    return resource_icon("brand", "logo.png")


def _logo_icon() -> QIcon:
    """标题栏图标：站点/产品 logo，取不到就留空。"""

    path = _logo_path()
    return QIcon(str(path)) if path is not None else QIcon()


def _window_title(detail: str | None = None) -> str:
    title = f"{logo_text()}::RCE DSPF Analyzer"
    return title if detail is None else f"{title}--{detail}"


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    return {"value": value}


class DspfMainWindow(AnalysisActionsMixin, OaBridgeMixin, SiMainWindow):
    indexLoaded = pyqtSignal(str)

    def __init__(
        self,
        *,
        cache_dir: str | None = None,
        bridge_enabled: bool = False,
        parent=None,
    ) -> None:
        super().__init__(_window_title(), parent, mark=_logo_icon())
        # 分析窗口没有菜单栏：标题栏下面直接是内容区。
        self.menu_bar.hide()
        self.cache_dir = cache_dir
        self._bridge_enabled = bridge_enabled
        self._bridge_open_path: Path | None = None
        self.source_path: Path | None = None
        self.index_path: Path | None = None
        self.current_net: int | str | None = None
        self.current_net_name: str | None = None
        self._last_select_error = ""
        self._started_at = 0.0
        self._pending: dict[int, str] = {}
        self._oa_context_available = all(os.environ.get(name) for name in (
            "RCE_DSPF_LAYOUT_LIB", "RCE_DSPF_LAYOUT_CELL", "RCE_DSPF_LAYOUT_VIEW",
        ))
        self.models = create_models()
        self.ui = build_workspace(self.models)
        self.setCentralWidget(self.ui.root)
        self.resize(1280, 780)
        self.setMinimumSize(900, 600)
        self._create_actions()
        self._create_status_bar()
        self._connect_workspace()
        self.indexer = IndexController(self)
        self.indexer.busyChanged.connect(self._set_busy)
        self.indexer.progress.connect(self._index_progress)
        self.indexer.completed.connect(self._index_completed)
        self.indexer.cancelled.connect(lambda: self._set_status("Indexing cancelled"))
        self.indexer.failed.connect(self._show_error)
        self._configure_analysis_workers()
        self.bridge = None
        if bridge_enabled:
            self._enable_bridge()
        self._set_busy(False)

    def _create_actions(self) -> None:
        style = self.style()
        self.open_action = QAction(style.standardIcon(QStyle.SP_DialogOpenButton), "Open", self)
        self.open_action.setShortcut(QKeySequence.Open)
        self.rebuild_action = QAction(style.standardIcon(QStyle.SP_BrowserReload), "Rebuild", self)
        self.cancel_action = QAction(style.standardIcon(QStyle.SP_DialogCancelButton), "Cancel", self)
        self.highlight_action = QAction(style.standardIcon(QStyle.SP_ArrowRight), "Highlight in Layout", self)
        self.oa_selection_action = QAction(style.standardIcon(QStyle.SP_ArrowLeft), "Use OA Selection", self)
        for action in (
            self.open_action,
            self.rebuild_action,
            self.cancel_action,
            self.highlight_action,
            self.oa_selection_action,
        ):
            action.setToolTip(action.text())
        toolbar = self.addToolBar("DSPF")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toolbar.addAction(self.open_action)
        toolbar.addAction(self.rebuild_action)
        toolbar.addAction(self.cancel_action)
        toolbar.addSeparator()
        toolbar.addAction(self.oa_selection_action)
        self.open_action.triggered.connect(self._choose_open)
        self.rebuild_action.triggered.connect(lambda: self._start_index(force=True))
        self.cancel_action.triggered.connect(self._cancel_index)
        self.highlight_action.triggered.connect(self._highlight_current_net)
        self.oa_selection_action.triggered.connect(self._request_oa_selection)

    def _create_status_bar(self) -> None:
        self.status_text = QLabel("Open a DSPF/SPF file to begin")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFixedWidth(220)
        self.progress.hide()
        self.issue_text = QLabel("Warnings 0  Errors 0")
        self.statusBar().addWidget(self.status_text, 1)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.issue_text)

    def _connect_workspace(self) -> None:
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self.ui.search.textChanged.connect(lambda: self._search_timer.start())
        self._search_timer.timeout.connect(self._apply_search)
        selection = self.ui.net_pane.table.selectionModel()
        selection.currentRowChanged.connect(self._net_selected)
        self.ui.net_pane.table.customContextMenuRequested.connect(
            self._show_net_context_menu
        )
        self.ui.tabs.currentChanged.connect(self._network_tab_changed)
        self.ui.path_pane.requested.connect(self._analyze_path)
        for model in self.models.values():
            model.loadFailed.connect(self._show_error)

    def open_path(self, path: str | Path, *, force: bool = False) -> None:
        candidate = Path(path).expanduser().resolve()
        if not candidate.is_file():
            self._show_error(f"File does not exist: {candidate}")
            return
        with candidate.open("rb") as stream:
            is_sqlite = stream.read(16) == b"SQLite format 3\x00"
        if not self._accept_open_path(candidate):
            return
        if is_sqlite:
            self.source_path = None
            if self._load_index(candidate):
                self._set_status(f"Index loaded: {candidate}")
            return
        self.source_path = candidate
        self._start_index(force=force)

    def _accept_open_path(self, candidate: Path) -> bool:
        if not self._bridge_enabled:
            return True
        if self._bridge_open_path is None:
            self._bridge_open_path = candidate
            return True
        if candidate == self._bridge_open_path:
            return True
        self._show_error(
            "Bridge mode is locked to its launch source: "
            f"{self._bridge_open_path}"
        )
        return False

    def _choose_open(self) -> None:
        if self._bridge_enabled:
            return
        path, _ = ask_file(
            self,
            "Open DSPF",
            ["DSPF files (*.dspf *.spf *.db *.sqlite *.sqlite3)", "All files (*)"],
        )
        if path:
            self.open_path(path)

    def _start_index(self, *, force: bool) -> None:
        if self.source_path is None:
            return
        self._started_at = time.monotonic()
        self.progress.setValue(0)
        self.indexer.start(str(self.source_path), cache_dir=self.cache_dir, force=force)
        self._set_status(f"Indexing {self.source_path.name}")

    def _cancel_index(self) -> None:
        self.indexer.cancel()
        self._set_status("Cancelling index build...")

    def _set_busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.open_action.setEnabled(not busy and not self._bridge_enabled)
        self.cancel_action.setEnabled(busy)
        self._update_actions()

    def _index_progress(self, progress: object) -> None:
        get = progress.get if isinstance(progress, Mapping) else lambda key, default=0: getattr(progress, key, default)
        read, total = int(get("bytes_read", 0)), int(get("total_bytes", 0))
        self.progress.setValue(int(1000 * read / total) if total else 0)
        elapsed = max(time.monotonic() - self._started_at, 0.001)
        rate = read / elapsed / (1024 * 1024)
        self._set_status(
            f"Line {int(get('line', 0)):,}  {rate:.1f} MiB/s  "
            f"Records {int(get('records', 0)):,}  Diagnostics {int(get('diagnostics', 0)):,}"
        )

    def _index_completed(self, result: object) -> None:
        self.issue_text.setText(
            f"Warnings {getattr(result, 'warning_count', 0):,}  Errors {getattr(result, 'error_count', 0):,}"
        )
        reused = "cache reused" if getattr(result, "reused", False) else "index complete"
        self._set_status(f"{reused}: {getattr(result, 'index_path')}")
        self._load_index(Path(getattr(result, "index_path")))

    def _load_index(self, path: Path) -> bool:
        from rcepy.dspf.repository import DspfRepository

        try:
            with DspfRepository(path) as repository:
                info = repository.info()
        except Exception as exc:
            self._show_error(f"Cannot open DSPF index: {exc}")
            return False
        self.index_path = path
        self._invalidate_analysis()
        warnings = int(info.get("warning_count", 0))
        errors = int(info.get("error_count", 0))
        self.issue_text.setText(f"Warnings {warnings:,}  Errors {errors:,}")
        indexed_source = Path(str(info.get("source_path", ""))).expanduser()
        if self.source_path is None and indexed_source.is_file():
            self.source_path = indexed_source
        self.models["nets"].set_index(path)
        for name, model in self.models.items():
            if name != "nets":
                model.set_index(path, reload=False)
        self.current_net = self.current_net_name = None
        self.ui.summary.set_summary(None)
        self.setWindowTitle(_window_title(path.name))
        self.title_bar.title.setText(_window_title(path.name))
        self._update_actions()
        self.indexLoaded.emit(str(path))
        return True

    def _apply_search(self) -> None:
        self.models["nets"].set_query(
            search=self.ui.search.text().strip() or None, exact_name=None
        )

    def _net_selected(self, current, _previous) -> None:
        self._invalidate_path()
        self._invalidate_network()
        row = self.models["nets"].row_at(current.row()) if current.isValid() else None
        if not row or self.index_path is None:
            self.current_net = self.current_net_name = None
            self.summary_queries.invalidate()
            self.ui.summary.set_summary(None)
            self._update_actions()
            return
        self.current_net = row.get("id", row.get("name"))
        self.current_net_name = str(row.get("name", self.current_net))
        self._queue_summary()
        for name, model in self.models.items():
            if name != "nets":
                model.set_query(net=self.current_net)
        self._queue_network_if_visible()
        self._update_actions()

    def select_net_by_name(self, name: str) -> bool:
        if self.index_path is None:
            self._last_select_error = "No DSPF index is open"
            return False
        from rcepy.dspf.repository import DspfRepository

        try:
            with DspfRepository(self.index_path) as repository:
                target = repository.get_net(name)
        except (KeyError, ValueError) as exc:
            self._last_select_error = str(exc)
            return False
        self._search_timer.stop()
        with QSignalBlocker(self.ui.search):
            self.ui.search.setText(name)
        self.models["nets"].set_query(search=None, exact_name=name)
        for row_number, row in enumerate(self.models["nets"].rows):
            if row.get("id") == target["id"]:
                self.ui.net_pane.table.setCurrentIndex(self.models["nets"].index(row_number, 0))
                self._last_select_error = ""
                return True
        self._last_select_error = f"Net is not visible after query: {name}"
        return False

    def _show_net_context_menu(self, position) -> None:
        table = self.ui.net_pane.table
        index = table.indexAt(position)
        if not index.isValid():
            return
        table.setCurrentIndex(index)
        menu = QMenu(table)
        menu.addAction(self.highlight_action)
        menu.exec_(table.viewport().mapToGlobal(position))

    def _update_actions(self) -> None:
        busy = hasattr(self, "indexer") and self.indexer.busy
        self.rebuild_action.setEnabled(not busy and self.source_path is not None)
        linked = (
            not busy and self.bridge is not None and self.index_path is not None
            and self._oa_context_available
        )
        self.highlight_action.setEnabled(
            linked and self.current_net_name is not None
            and not getattr(self, "_highlight_busy", False)
        )
        self.oa_selection_action.setEnabled(linked)

    def _set_status(self, message: str) -> None:
        self.status_text.setText(message)
        self.status_text.setToolTip(message)

    def _show_error(self, message: str) -> None:
        self._set_status(message)
        notice(self, "DSPF Analyzer", message)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.indexer.shutdown()
        self._shutdown_analysis()
        if self.bridge:
            self.bridge.close()
        event.accept()

    @staticmethod
    def _analysis_dict(value: Any) -> dict[str, Any]:
        return _as_dict(value)


__all__ = ["DspfMainWindow"]
