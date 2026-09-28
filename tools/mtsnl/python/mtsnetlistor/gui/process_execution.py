"""Submit generation and its validated, optionally frozen publication choices."""

from __future__ import annotations
from dataclasses import replace
from ..model_request import NetlistRequest
from ..model_design import TargetSelection
from ..generation_result import MultiGenerationResult
from .draft_requests import cell_spec


class ProcessExecution:
    def __init__(self, *, controller, generation, drafts, results, form, publication,
                 request, matches, save, defaults_busy, environment, cell_count, error, enable_run):
        self._controller = controller
        self.generation = generation
        self.drafts = drafts
        self._results = results
        self._form = form
        self._publication = publication
        self._request = request
        self._matches = matches
        self._save = save
        self._defaults_busy = defaults_busy
        self._environment = environment
        self._cell_count = cell_count
        self._error = error
        self._enable_run = enable_run

    def start(self, *, publish_after_generation: bool) -> bool:
        if self._defaults_busy():
            self._error("PDK defaults are still being loaded; wait for the probe to finish")
            return False
        try:
            full_request = self._request()
            publication_requested = self.has_publication(full_request)
            if publish_after_generation and publication_requested:
                target_catalog = self._controller().state.target_catalog
                if target_catalog is None or not target_catalog.authoritative:
                    raise ValueError(
                        "Target catalog is not authoritative; refresh with dbAccess before Run"
                    )
                target_catalog_data = getattr(target_catalog, "catalog", None)
                writable_libraries = {
                    str(library.name)
                    for library in getattr(
                        target_catalog_data, "libraries", ()
                    )
                    if getattr(library, "writable", False)
                }
                for spec in full_request.selected_cells:
                    target = spec.target or TargetSelection()
                    if not (
                        target.generate_symbol_view
                        or target.generate_netlist_view
                    ):
                        continue
                    if not target.library:
                        raise ValueError(
                            f"Target Library is required for {spec.library}/{spec.cell}/{spec.view}"
                        )
                    if (
                        target_catalog_data is not None
                        and target.library not in writable_libraries
                    ):
                        raise ValueError(
                            f"Target Library is not writable in the current project cds.lib: {target.library}"
                        )
            active_cells = self._cell_count()
            request = full_request
            if not active_cells:
                # Publication is a later, optional stage.  Generate an artifact
                # whose ownership digest contains only source/netlist inputs so
                # publication settings do not change run-artifact ownership.
                request = replace(request, target=TargetSelection()).validate()
        except Exception as exc:
            self._error(str(exc))
            return False
        self.generation.begin()
        self._results.clear()
        if active_cells:
            if self._environment() is None:
                token = self._controller().generate_many(request)
            else:
                token = self._controller().generate_many(
                    request,
                    environ=self._environment(),
                )
        else:
            if self._environment() is None:
                token = self._controller().generate(request)
            else:
                token = self._controller().generate(
                    request,
                    environ=self._environment(),
                )
        self.generation.submitted(
            token, request, multiple=bool(active_cells),
            publication=full_request if publish_after_generation and publication_requested else None,
        )
        self._enable_run(False)
        return True

    @staticmethod
    def has_publication(request: NetlistRequest) -> bool:
        return any(
            spec.target is not None
            and (
                spec.target.generate_symbol_view
                or spec.target.generate_netlist_view
            )
            for spec in request.selected_cells
        )

    def publish(self, frozen_request: NetlistRequest | None = None) -> None:
        if isinstance(self.generation.result, MultiGenerationResult):
            self.publish_many(frozen_request)
            return
        publication_requested = (
            self.has_publication(frozen_request)
            if frozen_request is not None
            else self._form.publication_requested()
        )
        if self.generation.result is None or not publication_requested:
            return
        target_catalog = self._controller().state.target_catalog
        if target_catalog is None or not target_catalog.authoritative:
            self._error("Target catalog is not authoritative; refresh with dbAccess before publication")
            return
        # Preserve source/model/process values from the generation that
        # created this artifact.  Only publication controls are read live.
        request = self.generation.request
        if request is None:
            self._error("No validated generation request is available")
            return
        if not self._matches(request):
            return
        try:
            target = (
                frozen_request.target
                if frozen_request is not None
                else TargetSelection(
                    library=self._publication.target_library.currentText() or None,
                    cell=self._publication.target_cell.text().strip() or None,
                    generate_symbol_view=self._publication.publish_symbol.isChecked(),
                    generate_netlist_view=self._publication.publish_text.isChecked(),
                    overwrite_symbol_view=self._publication.overwrite_symbol_view.isChecked(),
                    overwrite_netlist_view=self._publication.overwrite_netlist_view.isChecked(),
                )
            )
            request = replace(
                request,
                target=target.validate(request.source.cell),
            ).validate()
        except Exception as exc:
            self._error(str(exc))
            return
        publish_kwargs = {
            "netlist": self.generation.result.stable_output,
            "run_dir": self.generation.result.run_dir,
        }
        if self._environment() is not None:
            publish_kwargs["source_environment"] = self._environment()
        self._results.publication_requests = (request,)
        self._controller().publish(request, **publish_kwargs)

    def publish_many(self, frozen_request: NetlistRequest | None = None) -> None:
        result = self.generation.result
        if not isinstance(result, MultiGenerationResult):
            return
        target_catalog = self._controller().state.target_catalog
        if target_catalog is None or not target_catalog.authoritative:
            self._error("Target catalog is not authoritative; refresh with dbAccess before publication")
            return
        if self.generation.request is None:
            self._error("No validated multi-cell generation request is available")
            return
        if not self._matches(self.generation.request):
            return
        items = []
        try:
            # Publication choices are deliberately late-bound.  Keep each
            # generated request's source/model/options identity, while taking
            # the current target/view/overwrite controls from the cell map.
            self._save()
            frozen_targets = {
                (spec.library, spec.cell, spec.view): spec.target or TargetSelection()
                for spec in frozen_request.selected_cells
            } if frozen_request is not None else {}
            for item in result.cells:
                key = (
                    item.request.source.library,
                    item.request.source.cell,
                    item.request.source.view,
                )
                state = self.drafts.view(key, self._form.dialect) if key in self.drafts else None
                target = frozen_targets.get(key)
                if target is None:
                    target = (
                        (cell_spec(state).target or TargetSelection())
                        if state is not None
                        else item.request.target
                    )
                request = replace(item.request, target=target).validate()
                if not request.target.generate_symbol_view and not request.target.generate_netlist_view:
                    continue
                items.append((request, item.result.stable_output, item.result.run_dir))
            if not items:
                self._error("No simulation view is enabled for the selected cells")
                return
        except Exception as exc:
            self._error(str(exc))
            return
        self._results.publication_requests = tuple(request for request, _netlist, _run in items)
        if self._environment() is None:
            self._controller().publish_many(tuple(items))
        else:
            self._controller().publish_many(
                tuple(items),
                source_environment=self._environment(),
            )
