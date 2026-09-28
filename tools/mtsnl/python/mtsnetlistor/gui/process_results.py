"""Present generated artifacts and attach target views to matching source cells."""

from __future__ import annotations

from pathlib import Path
from ..model_request import NetlistRequest
from ..generation_result import MultiGenerationResult
from .result_browser import ResultBrowserDialog, ResultRow, ResultView
from .host_requests import emit_open_view_request


class ProcessResults:
    def __init__(self, session, enable_open, report_error, show_status, dialog_parent):
        self._session = session
        self._enable_open = enable_open
        self._report_error = report_error
        self._show_status = show_status
        self._dialog_parent = dialog_parent
        self.rows: tuple[ResultRow, ...] = ()
        self._dialog: ResultBrowserDialog | None = None
        self.publication_requests: tuple[NetlistRequest, ...] = ()

    def clear(self) -> None:
        self.rows = ()
        self.publication_requests = ()
        self._enable_open(False)
        if self._dialog is not None:
            self._dialog.set_rows(())

    def generated(
        self,
        generation: object,
        *,
        request: NetlistRequest | None = None,
    ) -> None:
        rows: list[ResultRow] = []
        if isinstance(generation, MultiGenerationResult):
            for item in generation.cells:
                source = item.request.source
                rows.append(
                    ResultRow(
                        source.library,
                        source.cell,
                        source.view,
                        Path(item.result.stable_output),
                    )
                )
        elif request is not None:
            source = request.source
            output = getattr(generation, "stable_output", None)
            if output is not None:
                rows.append(
                    ResultRow(
                        source.library,
                        source.cell,
                        source.view,
                        Path(output),
                    )
                )
        self.rows = tuple(rows)
        self._enable_open(bool(rows))
        if self._dialog is not None:
            self._dialog.set_rows(self.rows)

    def published(self, publication: object) -> None:
        results = publication if isinstance(publication, tuple) else (publication,)
        requests = self.publication_requests
        self.publication_requests = ()
        if not requests or not self.rows:
            return
        rows = {row.source_key: row for row in self.rows}
        for request, result in zip(requests, results):
            if getattr(result, "status", "") not in {
                "succeeded",
                "manual_cleanup_required",
            }:
                continue
            key = (
                request.source.library,
                request.source.cell,
                request.source.view,
            )
            row = rows.get(key)
            if row is not None:
                rows[key] = row.with_publication(result)
        self.rows = tuple(rows[row.source_key] for row in self.rows)
        if self._dialog is not None:
            self._dialog.set_rows(self.rows)

    def show(self, _signal_value=None) -> None:
        if not self.rows:
            self._report_error("No generated netlist result is available")
            return
        if self._dialog is None:
            self._dialog = ResultBrowserDialog(
                self.rows,
                open_view=self.open_view,
                report_error=self._report_error,
                parent=self._dialog_parent,
            )
        else:
            self._dialog.set_rows(self.rows)
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()

    def open_view(self, view: ResultView) -> None:
        if self._session() is None:
            raise RuntimeError(
                "opening an OA result view requires MTS Netlistor to be launched from Virtuoso"
            )
        if view.library not in self._session().target_library_paths:
            raise RuntimeError(
                f"result library is not in the current Virtuoso session: {view.library}"
            )
        emit_open_view_request(view.library, view.cell, view.view)
        self._show_status(f"Requested view: {view.label}")
