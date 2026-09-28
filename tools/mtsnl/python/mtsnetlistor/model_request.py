"""Validated single/multi-cell netlisting request composition."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from .errors import RequestValidationError
from .model_validation import SIMULATORS, validate_oa_name
from .model_entries import ModelEntry, CornerExport, _validate_models
from .model_options import (ProcessOptions, SimulatorOption, _validate_temperature,
                            _validate_process_options, _validate_simulator_options)
from .model_design import SourceDesign, TargetSelection


@dataclass(frozen=True)
class CellNetlistSpec:
    """Per-cell netlisting inputs for a multi-cell request.

    ``cds_lib`` and source startup/simrc files remain on :class:`SourceDesign`;
    this value only carries the OA design identity and the settings that may
    differ from one selected cell to the next.  An empty ``cell_specs`` tuple
    on :class:`NetlistRequest` denotes the legacy single-cell form.
    """

    library: str
    cell: str
    view: str = "schematic"
    models: tuple[ModelEntry, ...] = ()
    process_options: ProcessOptions = field(default_factory=ProcessOptions)
    simulator_options: tuple[SimulatorOption, ...] = ()
    dialect: str = ""
    target: Optional["TargetSelection"] = None
    corner_export: CornerExport = field(default_factory=CornerExport)
    temperature_mode: str = "fixed"

    def validate(self, default_dialect: str) -> "CellNetlistSpec":
        library = validate_oa_name(self.library, "source library")
        cell = validate_oa_name(self.cell, "source cell")
        view = validate_oa_name(self.view, "source view")
        dialect = str(self.dialect or default_dialect)
        if dialect not in SIMULATORS:
            raise RequestValidationError(f"unsupported simulator dialect: {dialect!r}")
        models = _validate_models(self.models)
        process_options = _validate_process_options(self.process_options, dialect)
        simulator_options = _validate_simulator_options(self.simulator_options, dialect)
        target = (self.target or TargetSelection()).validate(cell)
        temperature_mode = _validate_temperature(self.temperature_mode, process_options, simulator_options, dialect)
        if temperature_mode == "inherit" and self.corner_export.mode != "library":
            raise RequestValidationError(
                "inherited temperature requires Spectre corner library mode"
            )
        return CellNetlistSpec(
            library,
            cell,
            view,
            models,
            process_options,
            simulator_options,
            dialect,
            target,
            self.corner_export.validate(dialect),
            temperature_mode,
        )


@dataclass(frozen=True)
class NetlistRequest:
    source: SourceDesign
    dialect: str = "spectre"
    models: tuple[ModelEntry, ...] = ()
    process_options: ProcessOptions = field(default_factory=ProcessOptions)
    simulator_options: tuple[SimulatorOption, ...] = ()
    target: TargetSelection = field(default_factory=TargetSelection)
    schema_version: int = 1
    # Additive multi-cell form.  When empty, the legacy ``source`` and
    # top-level settings above describe one cell exactly as before.
    cell_specs: tuple[CellNetlistSpec, ...] = ()
    corner_export: CornerExport = field(default_factory=CornerExport)
    temperature_mode: str = "fixed"

    def validate(self) -> "NetlistRequest":
        if self.schema_version != 1:
            raise RequestValidationError(f"unsupported request schema version: {self.schema_version}")
        source = self.source.validate()
        dialect = str(self.dialect)
        if dialect not in SIMULATORS:
            raise RequestValidationError(f"unsupported simulator dialect: {dialect!r}")
        models = _validate_models(self.models)
        process_options = _validate_process_options(self.process_options, dialect)
        options = _validate_simulator_options(self.simulator_options, dialect)
        corner_export = self.corner_export.validate(dialect)
        temperature_mode = _validate_temperature(self.temperature_mode, process_options, options, dialect)
        if temperature_mode == "inherit" and corner_export.mode != "library":
            raise RequestValidationError(
                "inherited temperature requires Spectre corner library mode"
            )
        raw_specs = tuple(self.cell_specs)
        if raw_specs:
            specs = tuple(spec.validate(dialect) for spec in raw_specs)
            seen: set[tuple[str, str, str]] = set()
            for spec in specs:
                key = (spec.library, spec.cell, spec.view)
                if key in seen:
                    raise RequestValidationError(
                        f"duplicate source cell specification: {spec.library}/{spec.cell}/{spec.view}"
                    )
                seen.add(key)
            # The source identity remains the first selected cell for legacy
            # consumers.  Per-cell workers use ``cell_specs`` directly.
            source = SourceDesign(
                source.cds_lib,
                specs[0].library,
                specs[0].cell,
                specs[0].view,
                source.startup_file,
                source.simrc,
            ).validate()
            dialect = specs[0].dialect
            # Preserve the legacy aliases as an exact view of the first cell.
            # This keeps canonical serialization stable across save/load while
            # multi-cell workers continue to consume every explicit spec.
            models = specs[0].models
            process_options = specs[0].process_options
            options = specs[0].simulator_options
            target = specs[0].target or TargetSelection()
            corner_export = specs[0].corner_export
            temperature_mode = specs[0].temperature_mode
            if temperature_mode == "inherit" and corner_export.mode != "library":
                raise RequestValidationError(
                    "inherited temperature requires Spectre corner library mode"
                )
        else:
            specs = ()
            target = self.target
        return NetlistRequest(
            source,
            dialect,
            models,
            process_options,
            options,
            target.validate(source.cell),
            1,
            specs,
            corner_export,
            temperature_mode,
        )

    @property
    def selected_cells(self) -> tuple[CellNetlistSpec, ...]:
        """Return normalized per-cell settings, including legacy requests."""

        value = self.validate()
        if value.cell_specs:
            return value.cell_specs
        return (
            CellNetlistSpec(
                value.source.library,
                value.source.cell,
                value.source.view,
                value.models,
                value.process_options,
                value.simulator_options,
                value.dialect,
                value.target,
                value.corner_export,
                value.temperature_mode,
            ),
        )

    @property
    def output_suffix(self) -> str:
        if self.corner_export.mode == "library":
            return "_corners.scs"
        return ".spe" if self.dialect == "spectre" else ".sp"

    @property
    def text_view(self) -> str:
        if self.corner_export.mode == "library":
            return "spectre"
        return "spectreText" if self.dialect == "spectre" else "spiceText"
