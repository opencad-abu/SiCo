"""StarRC native OpenAccess parasitic-view output options."""

from __future__ import annotations

from .config import DesignContext, RceConfig
from .oa_view import (
    oa_library_definitions,
    resolve_view_target,
    starrc_mapping_files,
    view_kind,
)


def starrc_oa_output_options(
    cfg: RceConfig, ctx: DesignContext
) -> tuple[list[str], dict[str, str]]:
    """Return native OA writer commands and settings common.opt must preserve."""
    if view_kind(cfg) != "oa":
        raise ValueError("StarRC view output requires the native OA view kind")

    target = resolve_view_target(cfg, ctx)
    library_definitions = oa_library_definitions(cfg)
    device_mapping, layer_mapping = starrc_mapping_files(cfg)
    commands = [
        f"OA_LIB_DEF: {library_definitions}",
        f"OA_LIB_NAME: {target.library}",
        f"OA_CELL_NAME: {target.cell}",
        f"OA_VIEW_NAME: {target.view}",
        f"OA_DEVICE_MAPPING_FILE: {device_mapping}",
        f"OA_LAYER_MAPPING_FILE: {layer_mapping}",
    ]
    if ctx.input_type in {"OA", "SCH+GDS"}:
        commands.append(f"OA_CDLOUT_RUNDIR: {ctx.cdl_dir}")

    annotation = _annotation_view(cfg, ctx, ctx.name_source)
    if annotation is not None:
        commands.append(f"OA_PORT_ANNOTATION_VIEW: {' '.join(annotation)}")
        if ctx.name_source == "schematic":
            commands.append(
                f"OA_PROPERTY_ANNOTATION_VIEW: {' '.join(annotation)}"
            )

    requirements = {
        command.strip(): value.strip()
        for line in commands
        for command, separator, value in (line.partition(":"),)
        if separator
    }
    requirements.update(
        {
            "NETLIST_FORMAT": "OA",
            "HIERARCHICAL_SEPARATOR": "|",
        }
    )
    return commands, requirements


def _annotation_view(
    cfg: RceConfig, ctx: DesignContext, name_source: str
) -> tuple[str, str, str] | None:
    """Use an annotation view only when that namespace is OA-backed."""
    if name_source == "schematic" and ctx.input_type in {"OA", "SCH+GDS"}:
        section = "schematic"
        cell = ctx.source_cell
    elif name_source == "layout" and ctx.input_type in {"OA", "CDL+LAY"}:
        section = "layout"
        cell = ctx.layout_cell
    else:
        return None

    library = cfg.text(
        "input", section, "lib"
    ).strip()
    view = cfg.text(
        "input", section, "view"
    ).strip()
    if not library or not cell or not view:
        return None
    return library, cell, view
