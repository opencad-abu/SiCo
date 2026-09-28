"""Generate StarRC command files from validated extraction configuration."""

from __future__ import annotations

from pathlib import Path

from caddefaults import Defaults, resolve_defaults
from .starrc_defaults import StarDefaults

from .config import DesignContext, RceConfig
from .gen_query import generate_star_query
from .starrc_common import (
    reject_cumulative_common_commands,
    validate_parasitic_overrides,
)
from .starrc_inputs import (
    require_starrc_file,
    resolve_starrc_inputs,
    starrc_common_options,
)
from .starrc_oa import starrc_oa_output_options
from .starrc_options import (
    star_yes_no,
    starrc_filter_lines,
    starrc_hierarchical_separator,
    starrc_netlist_format,
    starrc_parasitic_info_lines,
    starrc_pin_order_file,
    starrc_selection_lines,
)
from .textutil import clean_lines, write_text


def generate_starrc(cfg: RceConfig, ctx: DesignContext, *, defaults: Defaults | None = None) -> Path:
    baseline = StarDefaults(resolve_defaults(("starrc.options",), defaults, ctx.log_dir), cfg)
    calibre_runset, calibre_query_file = _starrc_calibre_inputs(cfg, ctx)
    netlist_format = starrc_netlist_format(cfg)
    view_output = netlist_format == "OA"
    if cfg.is_multi_corner and view_output:
        raise ValueError(
            "StarRC multi-corner extraction currently requires a file netlist "
            "(DSPF/SPF or SPEF), not an OA view"
        )
    hierarchy = "|" if view_output else starrc_hierarchical_separator(cfg)
    pin_order_file = starrc_pin_order_file(cfg, ctx, netlist_format)
    force_unreduced, parasitic_info_lines, parasitic_requirements = (
        starrc_parasitic_info_lines(cfg, netlist_format)
    )
    star = cfg.section("extract", "starrc")
    if cfg.is_multi_corner:
        if cfg.text("extract", "starrc", "tcad_grd_file").strip() or cfg.text(
            "extract", "starrc", "mapping_file"
        ).strip():
            raise ValueError(
                "StarRC multi-corner extraction requires per-corner nxtgrd and "
                "mapping files; remove extract.starrc.tcad_grd_file and mapping_file"
            )
        corner_inputs = tuple(
            (corner, *resolve_starrc_inputs(cfg, corner))
            for corner in cfg.corners
        )
        if len(corner_inputs) > 15:
            raise ValueError("StarRC simultaneous multicorner extraction supports at most 15 corners")
        tcad_grd_file = mapping_file = None
    else:
        corner_inputs = ()
        tcad_grd_file, mapping_file = resolve_starrc_inputs(cfg)
    rc_type = RC_TYPE_STAR.get(
        cfg.text("extract", "rc_type"),
        cfg.text("extract", "rc_type", default="RC"),
    )
    cell_type = "SCHEMATIC" if ctx.top_cell_source == "schematic" else "LAYOUT"
    net_type = "SCHEMATIC" if ctx.name_source == "schematic" else "LAYOUT"
    if cfg.name_source_configured:
        xref = "YES" if ctx.name_source == "schematic" else "NO"
    else:
        xref = star_yes_no(star.get("xref", "YES"))
    if (
        cell_type == "SCHEMATIC"
        and xref != "YES"
        and ctx.source_cell != ctx.layout_cell
    ):
        if cfg.name_source_configured:
            raise ValueError(
                "StarRC cannot combine extract.top_cell_source='schematic' with "
                "extract.name_source='layout'; XREF:NO interprets BLOCK in the "
                "layout namespace"
            )
        raise ValueError(
            "StarRC schematic top-cell selection requires extract.starrc.xref = YES"
        )

    selection_lines, selection_requirements = starrc_selection_lines(
        cfg,
        ctx,
        net_type=net_type,
        cell_type=cell_type,
    )
    filter_lines, filter_requirements = starrc_filter_lines(cfg)
    if "COUPLING_ABS_THRESHOLD" in filter_requirements and star_yes_no(
        baseline.value("COUPLE_TO_GROUND") or "NO"
    ) != "NO":
        raise ValueError(
            "StarRC coupling-cap filters require COUPLE_TO_GROUND: NO in starrc.options"
        )
    parasitic_requirements.update(selection_requirements)
    parasitic_requirements.update(filter_requirements)

    oa_output_lines: list[str] = []
    if view_output:
        oa_output_lines, oa_requirements = starrc_oa_output_options(cfg, ctx)
        parasitic_requirements.update(oa_requirements)
        parasitic_requirements.update(
            {
                "BLOCK": ctx.top_cell,
                "CELL_TYPE": cell_type,
                "EXTRACTION": rc_type,
                "XREF": xref,
            }
        )
    elif parasitic_requirements:
        parasitic_requirements.update(
            {
                "NETLIST_FORMAT": netlist_format,
                "EXTRACTION": rc_type,
            }
        )
    if pin_order_file:
        parasitic_requirements.update(
            {"SPICE_SUBCKT_FILE": pin_order_file, "NETLIST_FORMAT": netlist_format}
        )
    common_options, common_opt = starrc_common_options(
        cfg, cfg.corners[0] if cfg.is_multi_corner else None
    )
    if "NET_TYPE" in selection_requirements:
        reject_cumulative_common_commands(
            common_options,
            {"NETS", "NETS_FILE"},
            include_base=ctx.run_dir,
            common_opt=common_opt,
            purpose="StarRC user net selection",
        )
    validate_parasitic_overrides(
        common_options,
        parasitic_requirements,
        include_base=ctx.run_dir,
        common_opt=common_opt,
        purpose=(
            "StarRC OA view output"
            if view_output
            else "StarRC parasitic netlist options"
        ),
    )
    coupling_report = cfg.path(
        "extract", "starrc", "coupling_report_file", default="cc.rep", base=ctx.run_dir
    )
    out_path = cfg.output_path(ctx)
    temperature = cfg.text(
        "extract", "temperature"
    ).strip()
    temperature_lines = [f"OPERATING_TEMPERATURE: {temperature}"] if temperature else []
    corners_file_lines: list[str] = []
    corner_control_lines: list[str] = []
    if cfg.is_multi_corner:
        corners_file = ctx.log_dir / "star.corners"
        for corner, grid, mapping in corner_inputs:
            corners_file_lines.extend(
                [
                    f"CORNER_NAME: {corner}",
                    f"TCAD_GRD_FILE: {grid}",
                    f"OPERATING_TEMPERATURE: {dict(cfg.corner_temperature_pairs()).get(corner, temperature)}",
                    f"MAPPING_FILE: {mapping}",
                    "",
                ]
            )
        write_text(corners_file, clean_lines(corners_file_lines))
        corner_control_lines = [
            "SIMULTANEOUS_MULTI_CORNER: YES",
            f"CORNERS_FILE: {corners_file}",
            f"SELECTED_CORNERS: {' '.join(cfg.corners)}",
        ]
    lines = [
        "* StarRC extraction command file generated by rcepy",
        "",
        "*** Database options",
        f"BLOCK: {ctx.top_cell}",
        "",
        "*** CCI flow",
        f"CALIBRE_RUNSET: {calibre_runset}",
        f"CALIBRE_QUERY_FILE: {calibre_query_file}",
        "",
        f"CASE_SENSITIVE: {'YES' if cfg.lvs_case_sensitive() else 'NO'}",
        f"HIERARCHICAL_SEPARATOR: {hierarchy}",
        "",
        "*** RC Extraction options",
        *([] if cfg.is_multi_corner else [f"TCAD_GRD_FILE: {tcad_grd_file}"]),
        *([] if cfg.is_multi_corner else [f"MAPPING_FILE: {mapping_file}"]),
        *([] if cfg.is_multi_corner else temperature_lines),
        *corner_control_lines,
        f"EXTRACTION: {rc_type}",
        *baseline.lines("extraction", force_unreduced=force_unreduced),
        "",
        "*** Coupling Caps options",
        *baseline.lines("coupling"),
        "COUPLE_TO_GROUND: NO" if "COUPLING_ABS_THRESHOLD" in filter_requirements and baseline.value("COUPLE_TO_GROUND") is None else "",
        f"COUPLING_REPORT_FILE: {coupling_report}",
        "",
        "*** XREF options",
        f"XREF: {xref}",
        *baseline.lines("xref"),
        f"CELL_TYPE: {cell_type}",
        f"NET_TYPE: {net_type}",
        "",
        "*** Database processing",
        *baseline.lines("database"),
        *selection_lines,
        *baseline.lines("after_selection"),
        "",
        "*** Netlist options",
        f"NETLIST_FORMAT: {netlist_format}",
        *oa_output_lines,
        *([] if view_output else baseline.lines("netlist.file")),
        "" if view_output else f"NETLIST_FILE: {out_path}",
        *([] if view_output else parasitic_info_lines),
        *filter_lines,
    ]
    if pin_order_file:
        lines.append(f"SPICE_SUBCKT_FILE: {pin_order_file}")
    if star.get("three_d_ic", False):
        lines.extend(["", "*** 3DIC flow", "3D_IC : YES"])
        if star.get("three_d_ic_subckt_file"):
            subckt_file = cfg.path("extract", "starrc", "three_d_ic_subckt_file")
            lines.append(f"3D_IC_SUBCKT_FILE:{subckt_file}")
    if common_options:
        lines.extend(["", *common_options])
    return write_text(ctx.log_dir / "star.cmd", clean_lines(lines))


def starrc_output_publications(
    cfg: RceConfig, ctx: DesignContext
) -> tuple[tuple[Path, Path], ...]:
    """Map StarRC SMC's ``<netlist>.<corner>`` names to RCE names."""
    published = cfg.output_paths(ctx)
    if not cfg.is_multi_corner or len(published) <= 1:
        return tuple((path, path) for path in published)
    base = Path(cfg.output_path(ctx))
    native = tuple(base.with_name(f"{base.name}.{corner}") for corner in cfg.corners)
    return tuple(zip(native, published))


def _starrc_calibre_inputs(cfg: RceConfig, ctx: DesignContext) -> tuple[Path, Path]:
    """Resolve the Calibre files referenced by a StarRC CCI command file.

    Paired layout/source inputs generate their own LVS runset.  External SVDB
    and CCI inputs do not, so they must use the RCE-specific runset selected in
    the GUI and persisted as ``lvs.runset_file``.
    """
    query_file = ctx.log_dir / "star.query.cmd"
    if ctx.input_type in {"SVDB", "CCI"}:
        runset_raw = cfg.text("lvs", "runset_file").strip()
        if not runset_raw:
            raise ValueError(
                f"StarRC {ctx.input_type} input requires lvs.runset_file "
                "(the RCE_LVS_FILE selection)"
            )
        runset_file = cfg.resolve_path(runset_raw)
        require_starrc_file(runset_file, "CALIBRE_RUNSET", nonempty=True)
        # CCI has no Calibre query stage; generate its command file alongside
        # the StarRC command so CALIBRE_QUERY_FILE always names a real file.
        if not query_file.is_file() or query_file.stat().st_size == 0:
            generate_star_query(ctx, query_file)
        require_starrc_file(query_file, "CALIBRE_QUERY_FILE", nonempty=True)
        return runset_file, query_file

    return ctx.log_dir / "lvs.cal", query_file


RC_TYPE_STAR = {
    "RCC": "RC",
    "RC": "RC",
    "R+Cg+Cc": "RC",
    "R+Cg": "RC",
    "R": "R",
    "CC": "C",
    "Cg+Cc": "C",
    "C": "C",
    "Cg": "C",
    "NONE": "NONE",
    "No-RC": "NONE",
}
