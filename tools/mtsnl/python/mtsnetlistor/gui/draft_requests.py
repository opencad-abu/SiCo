"""Pure conversion between editable drafts and frozen generation requests."""

from dataclasses import replace
from pathlib import Path

from ..model import (CellNetlistSpec, ModelEntry, NetlistRequest, ProcessOptions,
                     SimulatorOption, SourceDesign, TargetSelection)
from .cell_drafts import CellDraft, CellIdentity, PublicationSelection, SimulatorDraft
from .form_state import parse_enum_values


def publication_target(selection):
    return TargetSelection(
        library=selection.target_library or None, cell=selection.target_cell or None,
        generate_symbol_view=selection.publish_symbol,
        generate_netlist_view=selection.publish_text,
        overwrite_symbol_view=selection.overwrite_symbol,
        overwrite_netlist_view=selection.overwrite_text,
    )


def cell_spec(draft):
    settings = draft.simulator
    models = tuple(ModelEntry(Path(file.strip()), section.strip(), label.strip(), enabled=enabled)
                   for enabled, file, section, label in settings.models if file.strip())
    options = []
    for enabled, name, kind, value, enums in settings.options:
        enum_values = parse_enum_values(enums)
        if not name.strip() and not value and kind.casefold() == "string" and not enum_values:
            continue
        options.append(SimulatorOption(name.strip(), value, kind, enabled=enabled,
                                       enum_values=enum_values))
    return CellNetlistSpec(
        *draft.identity, models,
        ProcessOptions(temp=(settings.temp or None) if settings.temperature_mode == "fixed" else None,
                       tnom=settings.tnom, scale=settings.scale or None,
                       scalem=settings.scalem, reltol=settings.reltol, gmin=settings.gmin or None),
        tuple(options), draft.dialect, publication_target(draft.publication),
        settings.corner_export, settings.temperature_mode,
    )


def build_request(cds, drafts, *, startup_file=None, simrc=None, multiple=True):
    specs = tuple(cell_spec(draft) for draft in drafts)
    if not specs:
        raise ValueError("Source Cells is empty; select at least one source view")
    first = specs[0]
    return NetlistRequest(
        SourceDesign(cds, first.library, first.cell, first.view, startup_file, simrc),
        dialect=first.dialect, models=first.models, process_options=first.process_options,
        simulator_options=first.simulator_options, target=first.target,
        cell_specs=specs if multiple else (), corner_export=first.corner_export,
        temperature_mode=first.temperature_mode,
    ).validate()


def draft_from_spec(spec, dialect, text=None):
    settings, target = spec.process_options, spec.target or TargetSelection()
    raw = text if text is not None else tuple(
        "" if value is None else str(value) for value in (settings.temp, settings.scale, settings.gmin))
    return CellDraft(
        CellIdentity(spec.library, spec.cell, spec.view), spec.dialect or dialect,
        SimulatorDraft(
            models=tuple((row.enabled, str(row.file), row.section, row.label) for row in spec.models),
            temp=raw[0], scale=raw[1], gmin=raw[2], tnom=settings.tnom, scalem=settings.scalem,
            reltol=settings.reltol, corner_export=spec.corner_export,
            temperature_mode=spec.temperature_mode,
            options=tuple((row.enabled, row.name, row.value_type, row.value, ",".join(row.enum_values))
                          for row in spec.simulator_options),
        ),
        PublicationSelection(target.library or "", target.cell or "", target.generate_symbol_view,
                             target.overwrite_symbol_view, target.generate_netlist_view,
                             target.overwrite_netlist_view),
    )


def target_free_request(request):
    value = request.validate()
    return replace(value, target=TargetSelection(), cell_specs=tuple(
        replace(spec, target=TargetSelection()) for spec in value.cell_specs)).validate()
