"""Host process tabs and compose workspace and batch actions.

ProcessPage remains a same-object compatibility export until supported callers
import its owner directly. Legacy active-page forwarding is removed after
scripted integrations use explicit page references.
"""

from __future__ import annotations
from operator import attrgetter
from pathlib import Path
from PyQt5.QtGui import QColor
from cadgui.chrome import SiMainWindow
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QTabBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from ..environment import SessionDescriptor
from ..project import discover_projects
from ..model import (
    NetlistRequest,
)
from cadgui.branding import logo_text
from .process_batch import ProcessBatch, BatchProcess
from .workspace_actions import WorkspaceActions
from .process_page import ProcessPage


class MtsMainWindow(SiMainWindow):
    """Host isolated process pages and run them in deterministic order."""

    def __init__(
        self,
        *,
        session: SessionDescriptor | None = None,
        module_root: str | Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(f"{logo_text()}::MTS Netlistor", parent)
        # 主窗口没有菜单栏：标题栏下面直接是内容区。
        self.menu_bar.hide()
        self.session = session
        self._module_root = module_root
        self._project_names = discover_projects(module_root)
        self._pages: list[ProcessPage] = []
        self._batch = ProcessBatch(
            pages=self._configured_batch_processes,
            lock_ui=self._set_batch_ui_locked,
            buttons=self._set_batch_buttons,
            show_status=lambda message: self.statusBar().showMessage(message),
            parent=self,
        )

        self.resize(1420, 900)
        root = QWidget(self)
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        # Keep batch actions at the bottom of the host, aligned with the
        # per-process execution row inside each tab.
        self.host_execution_row = QHBoxLayout()
        self.load_config_button = QPushButton("Load Config…")
        self.load_config_button.clicked.connect(lambda checked: self._load_workspace_config(checked))
        self.save_config_button = QPushButton("Save Config…")
        self.save_config_button.clicked.connect(lambda checked: self._save_workspace_config(checked))
        self.run_all_button = QPushButton("Run All")
        self.run_all_button.setObjectName("runAllButton")
        # 家族语义：主操作红棕实底，取消是同一支红色文字。
        self.run_all_button.setProperty("bottomAction", True)
        self.run_all_button.setToolTip("Run every configured process tab in order")
        self.run_all_button.clicked.connect(self._run_all)
        self.cancel_all_button = QPushButton("Cancel All")
        self.cancel_all_button.setObjectName("cancelAllButton")
        self.cancel_all_button.setProperty("dialogDanger", True)
        self.cancel_all_button.setToolTip("Cancel the active process tab")
        self.cancel_all_button.clicked.connect(self._cancel_all)
        self.process_tabs = QTabWidget(root)
        self.process_tabs.setDocumentMode(True)
        self.process_tabs.setTabsClosable(True)
        self.process_tabs.tabCloseRequested.connect(self._close_process_tab)
        self.process_tabs.tabBarClicked.connect(self._tab_clicked)
        outer.addWidget(self.process_tabs, 1)

        self.host_execution_row.addWidget(self.load_config_button)
        self.host_execution_row.addWidget(self.save_config_button)
        self.host_execution_row.addStretch(1)
        self.host_execution_row.addWidget(self.run_all_button)
        self.host_execution_row.addWidget(self.cancel_all_button)
        outer.addLayout(self.host_execution_row)



        self._workspace = WorkspaceActions(
            configured=self._configured_pages, active=self._active_page,
            allocate=self._allocate_workspace_page, adopt=self._adopt_workspace_pages,
            tabs=self.process_tabs, batch_active=lambda: self._batch.active,
            show_status=lambda message: self.statusBar().showMessage(message),
            dialog_parent=self,
        )
        self._add_process_page()
        self._add_new_process_tab()
        self.cancel_all_button.setEnabled(False)
        self.statusBar().showMessage("Ready")

    def _active_page(self) -> ProcessPage | None:
        widget = self.process_tabs.currentWidget()
        return widget if isinstance(widget, ProcessPage) else None

    def statusBar(self):  # noqa: N802 - Qt-compatible spelling
        """Expose the active page status to existing integrations."""

        page = self._active_page() if "process_tabs" in self.__dict__ else None
        return page.statusBar() if page is not None else super().statusBar()

    def __getattr__(self, name: str):
        """Route the established single-page test/integration API to active tab."""

        pages = self.__dict__.get("_pages")
        if pages:
            page = self._active_page()
            if page is not None and hasattr(page, name):
                return getattr(page, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value) -> None:
        """Route writes for legacy page attributes to the active process tab."""

        namespace = getattr(self, "__dict__", {})
        pages = namespace.get("_pages")
        if name == "session" and pages:
            # Session identity is host-wide. Keep already-created pages and
            # pages added later on the same target Virtuoso boundary.
            super().__setattr__(name, value)
            for page in pages:
                page.session = value
                # The controller owns the session used by background catalog,
                # defaults, and netlisting workers.  Updating only the page
                # attribute leaves an already-created page running against
                # the previous Virtuoso boundary after a host-session swap.
                page.controller.set_session(value)
            return
        if (
            pages
            and (name in {"session", "controller"} or name not in namespace)
            and not hasattr(type(self), name)
        ):
            page = self._active_page()
            if page is not None and hasattr(page, name):
                setattr(page, name, value)
                return
        super().__setattr__(name, value)

    def _add_process_page(
        self, *, request: NetlistRequest | None = None, label: str | None = None
    ) -> ProcessPage:
        page = ProcessPage(
            session=self.session,
            project_names=self._project_names,
            module_root=self._module_root,
            parent=self.process_tabs,
        )
        self._connect_process_page(page)
        self._pages.append(page)
        placeholder_index = self._new_process_tab_index()
        index = self.process_tabs.insertTab(
            placeholder_index, page, label or f"Process{len(self._pages)}"
        )
        self.process_tabs.setCurrentIndex(index)
        if request is not None:
            page._apply_request_config(request)
        return page

    def _connect_process_page(self, page: ProcessPage) -> None:
        page.projectLabelChanged.connect(
            lambda project, current=page: self._update_process_tab_label(
                current, project
            )
        )

    def _default_process_label(self, page: ProcessPage) -> str:
        try:
            return f"Process{self._pages.index(page) + 1}"
        except ValueError:
            return "Process"

    def _update_process_tab_label(
        self, page: ProcessPage, project_name: str = ""
    ) -> None:
        index = self.process_tabs.indexOf(page)
        if index < 0:
            return
        project = str(project_name).strip()
        self.process_tabs.setTabText(
            index,
            project or self._default_process_label(page),
        )
        self.process_tabs.setTabToolTip(
            index,
            f"Project: {project}" if project else self._default_process_label(page),
        )

    def _new_process_tab_index(self) -> int:
        """Return the index reserved for the trailing New Process tab."""

        for index in range(self.process_tabs.count()):
            if self.process_tabs.widget(index).objectName() == "newProcessTab":
                return index
        return self.process_tabs.count()

    def _add_new_process_tab(self) -> None:
        placeholder = QWidget(self.process_tabs)
        placeholder.setObjectName("newProcessTab")
        index = self.process_tabs.addTab(placeholder, "New Process")
        self.process_tabs.setTabToolTip(index, "Create a new process setup")
        self.process_tabs.tabBar().setTabTextColor(index, QColor("#2e8b57"))
        # The placeholder is an insertion affordance, not a deletable page.
        self.process_tabs.tabBar().setTabButton(index, QTabBar.RightSide, None)

    def _renumber_process_tabs(self) -> None:
        for page in self._pages:
            index = self.process_tabs.indexOf(page)
            if index < 0:
                continue
            project = str(page.project_combo.currentData() or "").strip()
            self._update_process_tab_label(page, project)

    def _allocate_workspace_page(self):
        page = ProcessPage(session=None, project_names=self._project_names,
                           module_root=self._module_root)
        try:
            self._connect_process_page(page)
            page.session = self.session
            page.controller.set_session(self.session)
        except Exception:
            page.shutdown()
            page.setParent(None)
            page.deleteLater()
            raise
        return page

    def _adopt_workspace_pages(self, prepared):
        old_pages = tuple(self._pages)
        self.process_tabs.blockSignals(True)
        try:
            while self.process_tabs.count():
                self.process_tabs.removeTab(0)
            self._pages.clear()
            for process, page in prepared:
                page.setParent(self.process_tabs)
                self._pages.append(page)
                self.process_tabs.addTab(
                    page, str(page.project_combo.currentData() or "").strip()
                    or self._default_process_label(page),
                )
            self._add_new_process_tab()
            self.process_tabs.setCurrentWidget(self._pages[0])
        finally:
            self.process_tabs.blockSignals(False)
        for page in old_pages:
            page.shutdown()
            page.setParent(None)
            page.deleteLater()

    _workspace_config = property(attrgetter("_workspace.snapshot"))

    _save_workspace_config = property(attrgetter("_workspace.save"))

    _load_workspace_config = property(attrgetter("_workspace.load"))

    _replace_workspace = property(attrgetter("_workspace.replace"))

    _show_workspace_error = property(attrgetter("_workspace.error"))

    def _close_process_tab(self, index: int) -> None:
        """Delete one configured process tab and stop its isolated workers."""

        if index < 0 or index >= self.process_tabs.count():
            return
        page = self.process_tabs.widget(index)
        if not isinstance(page, ProcessPage):
            # In particular, keep the trailing New Process placeholder alive.
            return
        if self._run_all_active:
            self.statusBar().showMessage("Cancel Run All before deleting a process tab")
            return

        was_current = self.process_tabs.currentWidget() is page
        page.shutdown()
        try:
            self._pages.remove(page)
        except ValueError:
            return
        self.process_tabs.removeTab(index)
        page.setParent(None)
        page.deleteLater()

        if not self._pages:
            self._add_process_page()
        self._renumber_process_tabs()
        if was_current or self._active_page() is None:
            selected = self._pages[min(index, len(self._pages) - 1)]
            self.process_tabs.setCurrentWidget(selected)

    def _tab_clicked(self, index: int) -> None:
        if self.process_tabs.tabText(index) != "New Process":
            return
        if self._run_all_active:
            self.statusBar().showMessage(
                "Cancel Run All before creating a process tab"
            )
            self._batch.select_active()
            return
        self.process_tabs.removeTab(index)
        self._add_process_page()
        self._add_new_process_tab()

    _run_all_active = property(attrgetter("_batch.active"))
    _run_all_timer = property(attrgetter("_batch._timer"))

    @property
    def _run_all_current(self):
        current = self._batch.current
        return next((page for page in self._pages
                     if current is not None and id(page) == current.identity), None)

    def _configured_batch_processes(self):
        return [
            BatchProcess(
                identity=id(page),
                poll=page._poll_state, state=lambda p=page: p.controller.state,
                defaults_busy=lambda p=page: p.defaults.busy,
                handoff=lambda p=page: p.generation.handoff,
                result=lambda p=page: p.generation.result,
                run=page._run, cancel=page._cancel,
                select=lambda p=page: self.process_tabs.setCurrentWidget(p),
                label=lambda p=page: self.process_tabs.tabText(self.process_tabs.indexOf(p)),
            ) for page in self._configured_pages()
        ]

    def _set_batch_buttons(self, active):
        self.run_all_button.setEnabled(not active)
        self.cancel_all_button.setEnabled(active)

    _run_all = property(attrgetter("_batch.start"))

    def _configured_pages(self) -> list[ProcessPage]:
        """Return pages with a usable source request, excluding blank tabs."""

        configured: list[ProcessPage] = []
        for page in self._pages:
            if page.source_edit.text().strip():
                configured.append(page)
        return configured

    def _set_batch_ui_locked(self, locked: bool) -> None:
        """Prevent tab/config edits from replacing a controller mid-batch."""

        for page in self._pages:
            page.upper_workspace.setEnabled(not locked)
            page.run_button.setEnabled(not locked)
            page.cancel_button.setEnabled(not locked)
            # Logger and splitter stay enabled/readable while the form and
            # single-page execution controls are frozen. Cancel All remains
            # the sole batch cancellation action.
        self.load_config_button.setEnabled(not locked)
        self.save_config_button.setEnabled(not locked)

    # Explicit aliases make the host action easy to invoke from menus,
    # automation, and future keyboard shortcuts without relying on a widget
    # click.
    def run_all(self) -> None:
        self._run_all()

    def cancel_all(self) -> None:
        self._cancel_all()

    _run_next_process = property(attrgetter("_batch.next_process"))

    _poll_run_all = property(attrgetter("_batch.poll"))

    _finish_run_all = property(attrgetter("_batch.finish"))

    _cancel_all = property(attrgetter("_batch.cancel"))

    def closeEvent(self, event) -> None:
        self._cancel_all()
        for page in self._pages:
            page.shutdown()
        event.accept()


__all__ = ["MtsMainWindow", "ProcessPage"]
