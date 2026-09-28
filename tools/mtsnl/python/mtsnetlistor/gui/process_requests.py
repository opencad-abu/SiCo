"""Build requests from cell drafts and verify generation input identity."""

from __future__ import annotations
from pathlib import Path
from PyQt5.QtCore import Qt
from ..model_request import NetlistRequest
from ..model_design import SourceDesign, TargetSelection
from ..request_data import canonical_request_digest
from .cell_drafts import CellDraft
from .draft_requests import build_request, cell_spec, target_free_request


class ProcessRequests:
    _spec = staticmethod(cell_spec)
    _target_free = staticmethod(target_free_request)

    def __init__(self, *, drafts, form, source, source_edit, queue, legacy,
                 save, source_files, queue_mode, error, read_current):
        self.drafts = drafts
        self._form = form
        self._source = source
        self._source_edit = source_edit
        self._queue = queue
        self._legacy = legacy
        self._save = save
        self._source_files = source_files
        self._queue_mode = queue_mode
        self._error = error
        self._read_current = read_current

    def read(self) -> NetlistRequest:
        self._save()
        cds = Path(self._source_edit.text().strip())
        normalized_cds = cds.expanduser().resolve()
        if self._source.is_session_path(normalized_cds):
            self._source.warn(normalized_cds)
            raise ValueError(self._source.warning_text(normalized_cds))
        if self._source.path is None:
            self._source.path = normalized_cds
        elif normalized_cds != self._source.path:
            raise ValueError(
                "source cds.lib changed after catalog selection; refresh and select source views again"
            )
        active_keys = []
        for row in range(self._queue.count()):
            payload = self._queue.item(row).data(Qt.UserRole)
            if isinstance(payload, tuple) and len(payload) == 3:
                active_keys.append(tuple(str(value) for value in payload))
        # Preserve the old scripted/headless entry point: callers that set the
        # hidden identity fields directly still get a normal single-cell
        # NetlistRequest.
        if not active_keys:
            if self._queue_mode():
                raise ValueError("Source Cells is empty; select at least one source view")
            library = self._legacy[0].text().strip()
            cell = self._legacy[1].text().strip()
            view = self._legacy[2].text().strip() or "schematic"
            state = self._form.capture((library, cell, view))
            assert state is not None
            return self.for_draft(
                cds,
                state,
            )
        return build_request(
            cds, (self.drafts.view(key, self._form.dialect) for key in active_keys),
            startup_file=self._source_files()[0], simrc=self._source_files()[1],
        )

    def for_draft(self, cds: Path, state: CellDraft) -> NetlistRequest:
        return build_request(
            cds, (state,), startup_file=self._source_files()[0],
            simrc=self._source_files()[1], multiple=False,
        )

    def matches(self, generated_request: NetlistRequest) -> bool:
        """Reject publication when editable netlist inputs drifted post-run."""

        try:
            current_request = self._target_free(
                self.generation_inputs(generated_request)
            )
            expected_request = self._target_free(generated_request)
            current_digest = canonical_request_digest(current_request)
            expected_digest = canonical_request_digest(expected_request)
        except Exception as exc:
            self._error(str(exc))
            return False
        if current_digest != expected_digest:
            self._error(
                "Simulation/model settings changed after generation; regenerate before publication"
            )
            return False
        return True

    def generation_inputs(self, generated_request: NetlistRequest) -> NetlistRequest:
        """Read live netlist controls while retaining an immutable source identity.

        Catalog refreshes can clear the hidden compatibility lib/cell/view
        fields.  That is harmless for publication because the source identity
        is already part of the successful generation request; editable model,
        process, and simulator controls still come from the live form.  A real
        source-path change is rejected before this fallback is allowed.
        """

        raw_source = self._source_edit.text().strip()
        if raw_source:
            normalized_source = Path(raw_source).expanduser().resolve()
            if self._source.is_session_path(normalized_source):
                self._source.warn(normalized_source)
                raise ValueError(self._source.warning_text(normalized_source))
            if normalized_source != generated_request.source.cds_lib:
                raise ValueError("source cds.lib changed after generation; regenerate before publication")
        try:
            return self._read_current()
        except Exception as primary:
            value = generated_request.validate()
            if value.cell_specs:
                specs = []
                for selected in value.selected_cells:
                    key = (selected.library, selected.cell, selected.view)
                    state = self.drafts.view(key, self._form.dialect) if key in self.drafts else None
                    if state is None:
                        raise primary
                    specs.append(self._spec(state))
                first = specs[0]
                return NetlistRequest(
                    SourceDesign(
                        value.source.cds_lib,
                        first.library,
                        first.cell,
                        first.view,
                        value.source.startup_file,
                        value.source.simrc,
                    ),
                    dialect=first.dialect or value.dialect,
                    models=first.models,
                    process_options=first.process_options,
                    simulator_options=first.simulator_options,
                    target=TargetSelection(),
                    cell_specs=tuple(specs),
                ).validate()
            key = (value.source.library, value.source.cell, value.source.view)
            state = self._form.capture(key)
            if state is None:
                raise primary
            return self.for_draft(value.source.cds_lib, state)
