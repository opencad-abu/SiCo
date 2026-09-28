"""Coordinate one process page with its source selection and cell drafts."""

from __future__ import annotations
from .compat_attribute import compat_attribute
from pathlib import Path
from PyQt5.QtCore import QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import QMainWindow
from ..environment import SessionDescriptor
from ..project import ProjectContext
from ..model import (
    NetlistRequest,
)
from .controller import MtsController
from .cell_drafts import CellDraftStore
from .defaults_coordinator import DefaultsCoordinator
from .draft_requests import cell_spec, target_free_request
from .generation_coordinator import GenerationCoordinator
from .model_table import ModelTable
from .option_table import OptionTable
from .worker_log_stream import WorkerLogStream
from .process_diagnostics import ProcessDiagnostics
from .simulator_form import SimulatorForm
from .publication_form import PublicationForm
from .process_surface import ProcessSurface
from .process_results import ProcessResults
from .cell_form import CellForm
from .source_project import SourceProject
from .process_requests import ProcessRequests
from .process_execution import ProcessExecution
from .cell_selection import CellSelection
from .defaults_presentation import DefaultsPresentation
from .process_presenter import ProcessPresenter
from .process_context import ProcessContext
from .worker_log import clean_worker_log_line, safe_catalog_log, useful_worker_log


class ProcessPage(QMainWindow):
    projectLabelChanged = pyqtSignal(str)

    def __init__(
        self,
        *,
        session: SessionDescriptor | None = None,
        project_names: tuple[str, ...] = (),
        module_root: str | Path | None = None,
        parent=None,
    ) -> None:
        # 进程页是主窗口里的标签页，不是独立窗口：只保留自己的状态栏，
        # 无边框标题栏和 red tint 由主窗口的 chrome 与家族样式表提供。
        super().__init__(parent)
        self.session = session
        self._module_root = module_root
        self._project_names = tuple(project_names)
        self._log_stream = WorkerLogStream(lambda message: self.log.append(message))
        self._diagnostics = ProcessDiagnostics(
            lambda message: self.log.append(message),
            lambda message: self.statusBar().showMessage(message),
        )
        self.controller = MtsController(
            session=session,
            persistent_source=True,
            on_state=lambda state: self._receive_state(state),
            on_log=self._receive_worker_log,
        )
        self.drafts = CellDraftStore()
        self.defaults = DefaultsCoordinator(
            self.drafts, self.controller, self._source_design,
            lambda: self._source_environment, lambda message: self.log.append(message),
        )
        self.generation = GenerationCoordinator()
        self._results = ProcessResults(
            lambda: self.session,
            lambda enabled: self.open_result_button.setEnabled(enabled),
            self._show_error, lambda message: self.statusBar().showMessage(message), self,
        )
        self.resize(1420, 860)
        self._build_ui()
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(100)
        self._poll_timer.timeout.connect(self._poll_state)
        self._poll_timer.start()
        if session is not None:
            self.controller.refresh_target_catalog()

    def _build_ui(self) -> None:
        self._simulator_form = SimulatorForm()
        self._publication_form = PublicationForm()
        self._surface = ProcessSurface(
            self._project_names, self._simulator_form, self._publication_form, self,
        )
        self.setCentralWidget(self._surface)
        self._model_editor = ModelTable(self.models_table, self._netlist_settings_changed, self)
        self._option_editor = OptionTable(self.options_table, self._netlist_settings_changed)
        self._cell_form = CellForm(
            self._simulator_form, self._publication_form, self._model_editor,
            self._option_editor, self.corner_editor,
            update_title=lambda: self._update_cell_settings_title(),
            save=lambda: self._save_active_cell_state(), changed=self._netlist_settings_changed,
            has_active=lambda: self._active_cell_key is not None,
        )
        self._selection = CellSelection(
            browser=self.library_browser, queue=self.source_cells_list,
            title=self.cell_settings_frame,
            legacy=(self.source_library, self.source_cell, self.source_view),
            drafts=self.drafts, form=self._cell_form,
            invalidate=self._invalidate_generation,
            enqueue=lambda key, **kwargs: self._enqueue_defaults_probe(key, **kwargs),
            advance=lambda: self._start_next_defaults_probe(),
            remove_defaults=self.defaults.remove,
        )
        self._source = SourceProject(
            form=self._cell_form, combo=self.project_combo, source_edit=self.source_edit,
            module_root=self._module_root, session=lambda: self.session,
            label_changed=self.projectLabelChanged.emit,
            clear=lambda: self._clear_source_context(),
            invalidate=self._invalidate_generation, error=self._show_error,
            timings=self._append_project_timings, append=lambda message: self.log.append(message),
            refresh_catalog=lambda: self.controller.refresh_source_catalog,
            dialog_parent=self,
        )
        self._requests = ProcessRequests(
            drafts=self.drafts, form=self._cell_form, source=self._source,
            source_edit=self.source_edit, queue=self.source_cells_list,
            legacy=(self.source_library, self.source_cell, self.source_view),
            save=lambda: self._save_active_cell_state(),
            source_files=lambda: (self._source_startup_file, self._source_simrc),
            queue_mode=lambda: self._queue_mode, error=self._show_error,
            read_current=lambda: self._request(),
        )
        self._execution = ProcessExecution(
            controller=lambda: self.controller, generation=self.generation,
            drafts=self.drafts, results=self._results, form=self._cell_form,
            publication=self._publication_form, request=lambda: self._request(),
            matches=lambda request: self._generation_inputs_match(request),
            save=lambda: self._save_active_cell_state(), defaults_busy=lambda: self.defaults.busy,
            environment=lambda: self._source_environment, cell_count=self.source_cells_list.count,
            error=self._show_error, enable_run=self.run_button.setEnabled,
        )
        self._defaults_ui = DefaultsPresentation(
            defaults=self.defaults, drafts=self.drafts, selection=self._selection,
            form=self._cell_form, generation_result=lambda: self.generation.result,
            source_text=self.source_edit.text, controller_busy=lambda: self.controller.state.busy,
            enable_run=self.run_button.setEnabled,
            set_busy_cursor=lambda busy: self.setCursor(Qt.BusyCursor) if busy else self.unsetCursor(),
            invalidate=self._invalidate_generation,
        )
        self._presenter = ProcessPresenter(
            controller=lambda: self.controller, session=lambda: self.session,
            defaults=self.defaults, defaults_ui=self._defaults_ui, generation=self.generation,
            results=self._results, diagnostics=self._diagnostics, logs=self._log_stream,
            source=self._source, source_text=self.source_edit.text, form=self._cell_form,
            selection=self._selection, drafts=self.drafts, target=self.target_library,
            browser=self.library_browser, publish_queued=lambda: self._run_queued_publication(),
            error=self._show_error, append=lambda message: self.log.append(message),
            show_status=lambda message: self.statusBar().showMessage(message),
            enable_run=self.run_button.setEnabled,
        )
        self._context = ProcessContext(
            form=self._cell_form, source=self._source, source_edit=self.source_edit,
            selection=self._selection, queue=self.source_cells_list, browser=self.library_browser,
            legacy=(self.source_library, self.source_cell, self.source_view),
            presenter=self._presenter, drafts=self.drafts, defaults=self.defaults,
            cancel_defaults=self._cancel_defaults_probes,
            reset_worker=lambda: self.controller.reset_source_project(),
            invalidate=self._invalidate_generation, refresh=lambda: self._refresh_source(),
        )
        self._connect_surface()
        self._update_target_controls()
        self.statusBar().showMessage("Ready")

    def _connect_surface(self) -> None:
        self.project_combo.currentIndexChanged.connect(self._project_changed)
        self.source_edit.textChanged.connect(self._source_cdslib_changed)
        self._surface.browse_source.clicked.connect(self._browse_source)
        self._surface.refresh_source.clicked.connect(lambda _checked=False: self._refresh_source(force_refresh=True))
        self.source_cells_list.customContextMenuRequested.connect(self._source_cells_context_menu)
        self.source_cells_list.currentItemChanged.connect(self._active_source_cell_changed)
        self.library_browser.view_list.customContextMenuRequested.connect(self._source_view_context_menu)
        self.library_browser.viewActivated.connect(self._select_source_view_from_signal)
        self.library_browser.selectionChanged.connect(self._source_browser_selection_changed)
        self.models_table.cellChanged.connect(self._model_cell_changed)
        self.models_table.cellChanged.connect(self._netlist_settings_changed)
        self.models_table.cellDoubleClicked.connect(self._model_cell_double_clicked)
        for name in ("add", "remove", "duplicate"):
            getattr(self._surface, name + "_model").clicked.connect(getattr(self._model_editor, name))
        self._surface.move_model_up.clicked.connect(self._model_editor.move_up)
        self._surface.move_model_down.clicked.connect(self._model_editor.move_down)
        self.corner_editor.changed.connect(self._netlist_settings_changed)
        self.corner_editor.changed.connect(self._update_export_labels)
        self.open_result_button.clicked.connect(self._show_results)
        self.run_button.clicked.connect(self._run)
        self.cancel_button.clicked.connect(self._cancel)
        self.simulator.currentTextChanged.connect(self._simulator_changed)
        for field in (self.temp, self.scale, self.gmin):
            field.textChanged.connect(self._netlist_settings_changed)
        self.temperature_mode.currentIndexChanged.connect(self._temperature_mode_changed)
        for field in (self.tnom, self.scalem, self.reltol):
            field.valueChanged.connect(self._netlist_settings_changed)
        self.options_table.cellChanged.connect(self._netlist_settings_changed)
        for name in ("add", "remove", "duplicate"):
            getattr(self._simulator_form, name + "_option").clicked.connect(getattr(self._option_editor, name))
        self._simulator_form.move_option_up.clicked.connect(self._option_editor.move_up)
        self._simulator_form.move_option_down.clicked.connect(self._option_editor.move_down)
        self.publish_symbol.toggled.connect(self._update_target_controls)
        self.publish_text.toggled.connect(self._update_target_controls)
        self.target_library.currentTextChanged.connect(lambda _text: self._save_active_cell_state())
        self.target_cell.textChanged.connect(lambda _text: self._save_active_cell_state())
        self.overwrite_symbol_view.toggled.connect(lambda _checked: self._save_active_cell_state())
        self.overwrite_netlist_view.toggled.connect(lambda _checked: self._save_active_cell_state())

    # Compatibility widget aliases: remove after supported scripts use the composed views.
    gmin = compat_attribute("_simulator_form.gmin")
    options_table = compat_attribute("_simulator_form.options_table")
    reltol = compat_attribute("_simulator_form.reltol")
    scale = compat_attribute("_simulator_form.scale")
    scalem = compat_attribute("_simulator_form.scalem")
    simulator = compat_attribute("_simulator_form.simulator")
    temp = compat_attribute("_simulator_form.temp")
    temperature_mode = compat_attribute("_simulator_form.temperature_mode")
    tnom = compat_attribute("_simulator_form.tnom")
    _target_widgets = compat_attribute("_publication_form._target_widgets")
    overwrite_netlist_view = compat_attribute("_publication_form.overwrite_netlist_view")
    overwrite_symbol_view = compat_attribute("_publication_form.overwrite_symbol_view")
    publish_symbol = compat_attribute("_publication_form.publish_symbol")
    publish_text = compat_attribute("_publication_form.publish_text")
    target_cell = compat_attribute("_publication_form.target_cell")
    target_library = compat_attribute("_publication_form.target_library")
    add_model = compat_attribute("_surface.add_model")
    browse_source = compat_attribute("_surface.browse_source")
    cancel_button = compat_attribute("_surface.cancel_button")
    cell_list = compat_attribute("_surface.cell_list")
    cell_settings_frame = compat_attribute("_surface.cell_settings_frame")
    corner_editor = compat_attribute("_surface.corner_editor")
    duplicate_model = compat_attribute("_surface.duplicate_model")
    execution_row = compat_attribute("_surface.execution_row")
    library_browser = compat_attribute("_surface.library_browser")
    library_list = compat_attribute("_surface.library_list")
    log = compat_attribute("_surface.log")
    log_splitter = compat_attribute("_surface.log_splitter")
    main_splitter = compat_attribute("_surface.main_splitter")
    models_table = compat_attribute("_surface.models_table")
    move_model_down = compat_attribute("_surface.move_model_down")
    move_model_up = compat_attribute("_surface.move_model_up")
    open_result_button = compat_attribute("_surface.open_result_button")
    project_combo = compat_attribute("_surface.project_combo")
    project_prompt = compat_attribute("_surface.project_prompt")
    publish_button = compat_attribute("_surface.publish_button")
    refresh_source = compat_attribute("_surface.refresh_source")
    remove_model = compat_attribute("_surface.remove_model")
    run_button = compat_attribute("_surface.run_button")
    source_browser_box = compat_attribute("_surface.source_browser_box")
    source_cell = compat_attribute("_surface.source_cell")
    source_cell_filter = compat_attribute("_surface.source_cell_filter")
    source_cell_list = compat_attribute("_surface.source_cell_list")
    source_cells_box = compat_attribute("_surface.source_cells_box")
    source_cells_list = compat_attribute("_surface.source_cells_list")
    source_edit = compat_attribute("_surface.source_edit")
    source_library = compat_attribute("_surface.source_library")
    source_library_filter = compat_attribute("_surface.source_library_filter")
    source_library_list = compat_attribute("_surface.source_library_list")
    source_lists_splitter = compat_attribute("_surface.source_lists_splitter")
    source_prompt = compat_attribute("_surface.source_prompt")
    source_view = compat_attribute("_surface.source_view")
    source_view_filter = compat_attribute("_surface.source_view_filter")
    source_view_list = compat_attribute("_surface.source_view_list")
    source_workspace_splitter = compat_attribute("_surface.source_workspace_splitter")
    upper_workspace = compat_attribute("_surface.upper_workspace")
    view_list = compat_attribute("_surface.view_list")
    workspace_splitter = compat_attribute("_surface.workspace_splitter")
    _loading_cell_state = compat_attribute("_cell_form.loading", writable=True)
    _active_dialect = compat_attribute("_cell_form.dialect", writable=True)
    _temperature_mode_changed = compat_attribute("_cell_form.temperature_mode_changed")
    _update_export_labels = compat_attribute("_cell_form.update_export_labels")
    _project_context = compat_attribute("_source.project", writable=True)
    _source_environment = compat_attribute("_source.environment", writable=True)
    _source_context_path = compat_attribute("_source.path", writable=True)
    _target_refresh_requested = compat_attribute("_source.target_refresh_requested", writable=True)
    _browse_source = compat_attribute("_source.browse")
    _project_changed = compat_attribute("_source.project_changed")
    _select_project_name = compat_attribute("_source.select_project")

    def _refresh_source(self, *, force_refresh: bool = False):
        return self._source.refresh(force_refresh=force_refresh)

    _canonical_cds_lib_path = staticmethod(SourceProject.canonical_path)
    _is_current_session_cds_lib = compat_attribute("_source.is_session_path")
    _source_cds_lib_warning_text = compat_attribute("_source.warning_text")
    _warn_external_source_cds_lib = compat_attribute("_source.warn")
    _source_startup_file = compat_attribute("_context.startup_file", writable=True)
    _source_simrc = compat_attribute("_context.simrc", writable=True)
    _source_cdslib_changed = compat_attribute("_context.changed")
    _clear_source_context = compat_attribute("_context.clear")
    _defaults_busy_cursor = compat_attribute("_defaults_ui._busy_cursor")
    _cancel_defaults_probes = compat_attribute("_defaults_ui.cancel")
    _defaults_loading = compat_attribute("_defaults_ui.loading")
    _update_defaults_busy_cursor = compat_attribute("_defaults_ui.update_cursor")

    def _netlist_settings_changed(self, *_args, mark_defaults: bool = True) -> None:
        if self._loading_cell_state:
            return
        if self._active_cell_key is not None:
            self._save_active_cell_state(edited=mark_defaults)
        self._invalidate_generation("simulation or model settings changed")

    def _simulator_changed(self, dialect: str) -> None:
        if self._loading_cell_state:
            return
        self._save_active_cell_state(dialect=self._active_dialect)
        self._active_dialect = str(dialect) or "spectre"
        if self._active_cell_key is not None:
            self._load_cell_state(self.drafts.view(self._active_cell_key, self._active_dialect))
        self._netlist_settings_changed(mark_defaults=False)

    def _invalidate_generation(self, reason: str = "source selection changed") -> None:
        had_generation = self.generation.result is not None
        self.generation.invalidate(self.controller.state.generation)
        self._clear_result_rows()
        if had_generation:
            self.log.append(f"Generation invalidated: {reason}; regenerate before publication")

    _active_cell_key = compat_attribute("_selection.active", writable=True)
    _queue_mode = compat_attribute("_selection.queue_mode", writable=True)
    _source_view_context_menu = compat_attribute("_selection.view_menu")
    _source_cells_context_menu = compat_attribute("_selection.cell_menu")
    _select_source_view = compat_attribute("_selection.select")
    _select_source_view_from_signal = compat_attribute("_selection.activated")
    _delete_source_cell = compat_attribute("_selection.delete")
    _active_source_cell_changed = compat_attribute("_selection.current_changed")
    _update_cell_settings_title = compat_attribute("_selection.update_title")

    def _source_design(self, key):
        return self._context.design(key)

    _refresh_defaults_form = compat_attribute("_defaults_ui.refresh_form")

    def _enqueue_defaults_probe(self, key, *, dialects=None):
        return self._defaults_ui.enqueue(key, dialects=dialects)

    _start_next_defaults_probe = compat_attribute("_defaults_ui.advance")
    _apply_defaults_report = compat_attribute("_defaults_ui.apply")
    _source_browser_selection_changed = compat_attribute("_selection.browser_changed")
    _add_model = compat_attribute("_model_editor.add")
    _remove_model = compat_attribute("_model_editor.remove")
    _duplicate_model = compat_attribute("_model_editor.duplicate")
    _browse_model = compat_attribute("_model_editor.browse")
    _model_cell_double_clicked = compat_attribute("_model_editor.cell_double_clicked")
    _model_corners = staticmethod(ModelTable.corners)
    _corner_widget = compat_attribute("_model_editor.corner_widget")
    _populate_model_corners = compat_attribute("_model_editor.populate_corners")
    _model_corner_changed = compat_attribute("_model_editor.corner_changed")
    _model_cell_changed = compat_attribute("_model_editor.cell_changed")
    _set_model_row = compat_attribute("_model_editor.set_row")
    _model_cell_text = compat_attribute("_model_editor.cell_text")
    _model_rows = compat_attribute("_model_editor.rows")
    _set_model_rows = compat_attribute("_model_editor.set_rows")
    _capture_cell_state = compat_attribute("_cell_form.capture")
    _save_active_cell_state = compat_attribute("_selection.save")
    _clear_cell_form = compat_attribute("_cell_form.clear")
    _load_cell_state = compat_attribute("_cell_form.load")
    _set_target_library_value = compat_attribute("_cell_form.set_target_library")
    _move_model = compat_attribute("_model_editor.move")
    _move_model_up = compat_attribute("_model_editor.move_up")
    _move_model_down = compat_attribute("_model_editor.move_down")
    _add_option = compat_attribute("_option_editor.add")
    _remove_option = compat_attribute("_option_editor.remove")
    _duplicate_option = compat_attribute("_option_editor.duplicate")
    _option_rows = compat_attribute("_option_editor.rows")
    _set_option_rows = compat_attribute("_option_editor.set_rows")
    _move_option = compat_attribute("_option_editor.move")
    _move_option_up = compat_attribute("_option_editor.move_up")
    _move_option_down = compat_attribute("_option_editor.move_down")

    def _request(self) -> NetlistRequest:
        return self._requests.read()

    _request_for_state = compat_attribute("_requests.for_draft")
    _state_to_spec = staticmethod(cell_spec)
    _target_free_request = staticmethod(target_free_request)
    _generation_inputs_match = compat_attribute("_requests.matches")
    _request_for_generation_inputs = compat_attribute("_requests.generation_inputs")

    def _run(self, _signal_value=None) -> bool:
        return self._start_generation(publish_after_generation=True)

    def _generate(self) -> bool:
        """Compatibility entry point for generation-only scripted callers."""

        return self._start_generation(publish_after_generation=False)

    _start_generation = compat_attribute("_execution.start")

    def _cancel(self, _signal_value=None) -> None:
        self.generation.cancel()
        self._cancel_defaults_probes()
        self.controller.cancel()

    def _run_queued_publication(self) -> None:
        request = self.generation.take_publication(
            canceled=self.controller.state.stage == "canceled")
        if request is not None:
            self._publish(request)
        self.run_button.setEnabled(not self.controller.state.busy and not self.defaults.busy)

    _request_has_publication = staticmethod(ProcessExecution.has_publication)

    def _publish(self, frozen_request: NetlistRequest | None = None):
        return self._execution.publish(frozen_request)

    _publish_many = compat_attribute("_execution.publish_many")

    def _apply_request_config(self, request, *, refresh_catalog=True, presentation=(), project=""):
        return self._context.apply(request, refresh_catalog=refresh_catalog,
                                   presentation=presentation, project=project)

    _pending_state = compat_attribute("_presenter.pending", writable=True)
    _catalog = compat_attribute("_presenter.source_catalog", writable=True)
    _displayed_source_catalog = compat_attribute("_presenter.source_catalog", writable=True)
    _displayed_target_catalog = compat_attribute("_presenter.target_catalog", writable=True)
    _target_catalog_authoritative = compat_attribute("_presenter.target_authoritative", writable=True)
    _displayed_publication = compat_attribute("_presenter.publication", writable=True)
    _receive_state = compat_attribute("_presenter.receive")

    def _receive_worker_log(self, message: str) -> None:
        self._log_stream.receive(message)

    _clean_worker_log_line = staticmethod(clean_worker_log_line)
    _safe_catalog_log = staticmethod(safe_catalog_log)
    _useful_worker_log = staticmethod(useful_worker_log)

    def _drain_worker_logs(self, *, flush_tail: bool = False) -> None:
        self._log_stream.drain(flush_tail=flush_tail)

    _poll_state = compat_attribute("_presenter.poll")
    _publication_requested = compat_attribute("_cell_form.publication_requested")
    _update_target_controls = compat_attribute("_cell_form.update_target_controls")
    _populate_source_tree = compat_attribute("_presenter.source_tree")

    def _append_source_catalog_diagnostics(self, result) -> None:
        self._diagnostics.source_catalog(result)

    def _append_project_timings(self, context: ProjectContext) -> None:
        self._diagnostics.project(context)

    _populate_target_libraries = compat_attribute("_presenter.target_libraries")

    def _show_error(self, message: str) -> None:
        self.log.append(f"Error: {message}")
        self.statusBar().showMessage(message)

    _result_rows = compat_attribute("_results.rows")
    _result_dialog = compat_attribute("_results._dialog")
    _publication_requests = compat_attribute("_results.publication_requests", writable=True)
    _clear_result_rows = compat_attribute("_results.clear")
    _set_generation_result_rows = compat_attribute("_results.generated")
    _apply_publication_result_rows = compat_attribute("_results.published")
    _show_results = compat_attribute("_results.show")
    _open_result_view = compat_attribute("_results.open_view")

    def _append_publication_diagnostics(self, publication) -> None:
        self._diagnostics.publication(publication)

    def closeEvent(self, event) -> None:
        self._cancel_defaults_probes()
        self.controller.close(wait=False)
        event.accept()


    def shutdown(self) -> None:
        """Stop this page's workers before the page is removed from a tab."""

        if hasattr(self, "_poll_timer"):
            self._poll_timer.stop()
        self._cancel_defaults_probes()
        # Tab/workspace replacement can immediately start a new source OCEAN
        # worker.  Wait for the canceled executor to drain so the removed page
        # cannot continue touching OA or run artifacts after its replacement
        # has committed.
        self.controller.close(wait=True)
