"""Load workspace candidates transactionally and serialize configured process pages."""

from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING
from cadgui.prompts import ask_file, ask_save_file
from ..workspace_model import WorkspaceConfig, WorkspaceProcess, WorkspaceCellPresentation
from ..workspace_input import load_workspace
from ..workspace_output import save_workspace

if TYPE_CHECKING:
    from .process_page import ProcessPage


class WorkspaceActions:
    def __init__(self, *, configured, active, allocate, adopt, tabs, batch_active,
                 show_status, dialog_parent):
        self._configured = configured
        self._active = active
        self._allocate = allocate
        self._adopt = adopt
        self._tabs = tabs
        self._batch_active = batch_active
        self._show_status = show_status
        self._dialog_parent = dialog_parent

    def snapshot(self) -> WorkspaceConfig:
        processes: list[WorkspaceProcess] = []
        for index, page in enumerate(self._configured(), start=1):
            tab_index = self._tabs.indexOf(page)
            label = (
                self._tabs.tabText(tab_index)
                if tab_index >= 0
                else f"Process{index}"
            )
            try:
                request = page._request()
            except Exception as exc:
                raise ValueError(f"{label}: {exc}") from exc
            presentation_rows = []
            for spec in request.selected_cells:
                key = (spec.library, spec.cell, spec.view)
                draft = (page.drafts.view(key, spec.dialect) if key in page.drafts
                         else page._capture_cell_state(key, dialect=spec.dialect))
                settings = draft.simulator
                text_values = (settings.temp, settings.scale, settings.gmin)
                presentation_rows.append(
                    WorkspaceCellPresentation(
                        spec.library,
                        spec.cell,
                        spec.view,
                        *text_values,
                    )
                )
            presentation = tuple(presentation_rows)
            processes.append(
                WorkspaceProcess(
                    label,
                    request,
                    presentation,
                    str(page.project_combo.currentData() or ""),
                )
            )
        return WorkspaceConfig(tuple(processes)).validate()

    def save(self, _signal_value=None) -> None:
        if self._batch_active():
            self._show_status("Cancel Run All before saving configuration")
            return
        path, _ = ask_save_file(self._dialog_parent, "Save MTS workspace configuration",
                                ["MTS workspace (*.toml)"])
        if not path:
            return
        try:
            destination = save_workspace(self.snapshot(), path)
        except Exception as exc:
            self.error(str(exc))
            return
        self._show_status(f"Configuration saved: {destination}")
        page = self._active()
        if page is not None:
            page.log.append(f"Configuration saved: {destination}")

    def load(self, _signal_value=None) -> None:
        if self._batch_active():
            self._show_status("Cancel Run All before loading configuration")
            return
        path, _ = ask_file(self._dialog_parent, "Load MTS workspace configuration",
                           ["MTS workspace (*.toml)"])
        if not path:
            return
        try:
            workspace = load_workspace(path)
            for process in workspace.processes:
                source = process.request.source.cds_lib
                page = self._active()
                if page is not None and page._is_current_session_cds_lib(source):
                    page._warn_external_source_cds_lib(source)
                    raise ValueError(page._source_cds_lib_warning_text(source))
            self.replace(workspace)
        except Exception as exc:
            self.error(str(exc))
            return
        source = Path(path).expanduser().resolve()
        self._show_status(f"Configuration loaded: {source}")
        page = self._active()
        if page is not None:
            page.log.append(f"Configuration loaded: {source}")

    def replace(self, workspace: WorkspaceConfig) -> None:
        """Replace all process tabs after the complete file has validated."""

        value = workspace.validate()
        prepared: list[tuple[WorkspaceProcess, ProcessPage]] = []
        # Register each page immediately after construction.  Applying a
        # request can fail (for example when a source cds.lib is rejected by
        # the current-session guard), and a page that has not yet been added
        # to ``prepared`` still owns a controller/timer that must be stopped.
        allocated: list[ProcessPage] = []
        try:
            for process in value.processes:
                page = self._allocate()
                allocated.append(page)
                if process.presentation:
                    if process.project:
                        page._apply_request_config(
                            process.request,
                            refresh_catalog=False,
                            presentation=process.presentation,
                            project=process.project,
                        )
                    else:
                        page._apply_request_config(
                            process.request,
                            refresh_catalog=False,
                            presentation=process.presentation,
                        )
                else:
                    if process.project:
                        page._apply_request_config(
                            process.request,
                            refresh_catalog=False,
                            project=process.project,
                        )
                    else:
                        page._apply_request_config(
                            process.request,
                            refresh_catalog=False,
                        )
                prepared.append((process, page))
        except Exception:
            for page in allocated:
                page.shutdown()
                page.setParent(None)
                page.deleteLater()
            raise

        self._adopt(prepared)
        # Start source catalog refreshes only after the swap. This prevents a
        # hidden candidate page from spawning a worker before the transaction
        # has committed, while each committed page still uses its own
        # controller and source cds.lib.
        for process, page in prepared:
            try:
                page._refresh_source()
            except Exception as exc:
                # The tab swap is already committed at this point.  Keep the
                # loaded request visible and report a refresh failure in that
                # process tab instead of allowing a synchronous controller or
                # path error to escape the Qt callback and leave the host in a
                # half-replaced state.
                page._show_error(
                    f"{process.name}: source catalog refresh failed: {exc}"
                )

    def error(self, message: str) -> None:
        page = self._active()
        if page is not None:
            page.log.append(f"Error: {message}")
        self._show_status(message)
