"""Coordinate multi-cell generation and effective defaults queries.

Legacy result/single-job imports reference their sole domain owners. Remove
these exports after supported callers migrate to the corresponding modules."""

from __future__ import annotations

from dataclasses import replace
from threading import Event
from typing import Optional, Mapping, Callable
from .model_request import NetlistRequest, CellNetlistSpec
from .model_design import SourceDesign, TargetSelection
from .defaults_request import DefaultsProbeRequest, MaeSetup
from .defaults_result import SourceDefaults
from .defaults_effective import source_defaults_from_report
from .defaults_workflow import probe_defaults
from .generation_workflow import generate
from .generation_result import GenerationResult, CellGenerationResult, MultiGenerationResult
from .errors import MtsNetlistorError


def read_source_defaults(
    source: SourceDesign,
    *,
    dialect: str = "spectre",
    environ: Optional[Mapping[str, str]] = None,
    ocean: Optional[str] = None,
    virtuoso: Optional[str] = None,
    timeout: float = 180.0,
    cancel_event: Optional[Event] = None,
    output_callback: Optional[Callable[[str], object]] = None,
    provider: str = "asi_initialization",
    mae_setup: MaeSetup | None = None,
) -> SourceDefaults:
    """Probe PDK-initialized ASI defaults in an isolated source worker.

    The default provider reads ASI initialization.  ``provider='mae_test'``
    requires an explicit :class:`MaeSetup` identity and reads that test's
    environment/simulator options with ``mae*`` in the isolated worker.
    """

    report = probe_defaults(
        DefaultsProbeRequest(source, dialect, provider, mae_setup),
        environ=environ,
        ocean=ocean or virtuoso,
        timeout=timeout,
        cancel_event=cancel_event,
        output_callback=output_callback,
    )
    return source_defaults_from_report(report, source)


def _request_for_cell(request: NetlistRequest, spec: CellNetlistSpec) -> NetlistRequest:
    """Build a single-cell request while retaining source worker settings."""

    source = request.source
    return NetlistRequest(
        SourceDesign(
            source.cds_lib,
            spec.library,
            spec.cell,
            spec.view,
            source.startup_file,
            source.simrc,
        ),
        dialect=spec.dialect or request.dialect,
        models=spec.models,
        process_options=spec.process_options,
        simulator_options=spec.simulator_options,
        # Publication remains a later, per-result operation.  Carry the
        # cell-specific target selection so the GUI can publish each result
        # without collapsing several cells onto one shared destination.
        target=spec.target or TargetSelection(),
        corner_export=spec.corner_export,
        temperature_mode=spec.temperature_mode,
    ).validate()


def generate_many(
    request: NetlistRequest,
    *,
    environ: Optional[Mapping[str, str]] = None,
    virtuoso: Optional[str] = None,
    ocean: Optional[str] = None,
    timeout: float = 600.0,
    cancel_event: Optional[Event] = None,
    output_callback: Optional[Callable[[str], object]] = None,
    allow_non_authoritative: bool = False,
) -> MultiGenerationResult:
    """Generate each selected source cell in its own OCEAN design session.

    Calls are sequential with a private run directory per cell. GUI project
    calls reuse the PDK process while standalone calls create new processes.
    ``generate()`` remains the single-cell API used by existing callers.
    """

    value = request.validate()
    specs = value.selected_cells
    if len(specs) <= 1 and not value.cell_specs:
        one = generate(
            value,
            environ=environ,
            virtuoso=virtuoso,
            ocean=ocean,
            timeout=timeout,
            cancel_event=cancel_event,
            output_callback=output_callback,
            allow_non_authoritative=allow_non_authoritative,
        )
        return MultiGenerationResult("succeeded", (CellGenerationResult(value, one),))
    results: list[CellGenerationResult] = []
    for spec in specs:
        if cancel_event is not None and cancel_event.is_set():
            raise MtsNetlistorError("multi-cell generation canceled")
        child = _request_for_cell(value, spec)
        generation_request = replace(child, target=TargetSelection()).validate()
        result = generate(
            generation_request,
            environ=environ,
            virtuoso=virtuoso,
            ocean=ocean,
            timeout=timeout,
            cancel_event=cancel_event,
            output_callback=output_callback,
            allow_non_authoritative=allow_non_authoritative,
        )
        # Keep the late-bound publication selection beside the generated
        # result, while the immutable run ownership digest remains target-free.
        results.append(CellGenerationResult(child, result))
    return MultiGenerationResult("succeeded", tuple(results))


__all__ = ["GenerationResult", "CellGenerationResult", "MultiGenerationResult", "generate", "generate_many", "probe_defaults", "read_source_defaults"]
