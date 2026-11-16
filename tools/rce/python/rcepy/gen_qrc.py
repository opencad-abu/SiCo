"""Generate Quantus command files from validated extraction configuration."""

from __future__ import annotations

from pathlib import Path

from caddefaults import Defaults, resolve_defaults
from .qrc_defaults import QrcDefaults

from .config import DesignContext, RceConfig
from .oa_view import (
    oa_library_definitions,
    qrc_layout_view,
    resolve_view_target,
    view_kind,
)
from .qrc_cells import qrc_cell_options
from .qrc_commands import qrc_extract_commands, qrc_lines
from .qrc_options import (
    qrc_floating_net_option,
    qrc_filter_cap,
    qrc_filter_res,
    qrc_parasitic_info_options,
    qrc_pin_order_file,
)
from .qrc_outputs import qrc_output_publications as qrc_output_publications
from .qrc_outputs import qrc_output_target, qrc_per_corner_output
from .qrc_technology import extract_tech_dirs, qrc_technology_options
from .textutil import clean_lines, q, write_text
from .finfet_qrc import QRC_PROCESS_DEFAULTS, qrc_finfet_technology
from .qrc_paths import qrc_path_text


def generate_qrc(cfg: RceConfig, ctx: DesignContext, *, defaults: Defaults | None = None) -> Path:
    finfet = cfg.flag("extract", "finfet_flow")
    if finfet and any(char.isspace() for char in str(ctx.run_dir)):
        raise ValueError("Quantus requires a run directory without whitespace; choose another run.run_dir")
    baseline = QrcDefaults(
        resolve_defaults(("quantus.options",), defaults, ctx.log_dir),
        overrides=QRC_PROCESS_DEFAULTS if finfet else None,
    )
    rc_type = RC_TYPE_QRC.get(
        cfg.text("extract", "rc_type"),
        cfg.text("extract", "rc_type"),
    )
    requested_output_type = cfg.output_type.strip().casefold()
    output_type = QRC_OUTPUT_FORMAT_ALIASES.get(
        requested_output_type, requested_output_type
    )
    output_file = ""
    input_db_extra: list[str] = []
    output_db_extra: list[str] = []
    netlist_output = True
    cdl_map_directory = ""
    if ctx.input_type in {"OA", "SCH+GDS"}:
        cdl_map_directory = (
            f'-cdl_out_map_directory "{q(str(ctx.cdl_dir))}"'
        )
    if cfg.is_view_output and ctx.input_type in {"CDL+GDS", "CDL+LAY", "SVDB", "CCI"}:
        raw_directory = cfg.text("input", "cdl", "run_directory").strip()
        directory = cfg.resolve_path(raw_directory) if raw_directory else None
        if directory is None or not directory.is_dir() or not (directory / "si.env").is_file():
            raise ValueError("Set input.cdl.run_directory to a valid CDL export directory containing si.env for QRC OA view output")
        cdl_map_directory = f'-cdl_out_map_directory "{q(str(directory))}"'
    if output_type in QRC_FILE_OUTPUT_TYPES:
        qrc_output_type = QRC_FILE_OUTPUT_TYPES[output_type]
        output_target = qrc_output_target(cfg, ctx, output_type)
        if finfet and qrc_per_corner_output(cfg, output_type) and any(
            char.isspace() for char in Path(output_target).name
        ):
            raise ValueError("Quantus multi-corner output filename must not contain whitespace")
        output_file = f'-file_name "{q(output_target)}"'
    elif cfg.is_view_output:
        target = resolve_view_target(cfg, ctx)
        kind = view_kind(cfg)
        if kind not in {"smart", "extracted", "idspf"}:
            raise ValueError(f"Unsupported Quantus OA view kind: {kind}")
        if kind == "idspf" and ctx.input_type not in {"OA", "SCH+GDS"}:
            raise ValueError(
                "Interactive DSPF (iDSPF) requires OA or SCH+GDS input with a schematic"
            )
        output_type = {"smart": "smartview", "extracted": "extview"}.get(kind, kind)
        qrc_output_type = {
            "smart": "smart_view",
            "extracted": "extracted_view",
            "idspf": "idspf",
        }[kind]
        netlist_output = False
        layout_view = qrc_layout_view(cfg, ctx)
        library_definitions = oa_library_definitions(cfg)
        input_db_extra.extend(
            [
                f'-design_cell_name "{q(target.cell)} {q(layout_view)} '
                f'{q(target.library)}"',
                f'-library_definitions_file "{q(str(library_definitions))}"',
            ]
        )
        if kind == "idspf":
            schematic_view = cfg.text(
                "input", "schematic", "view", default="schematic"
            ).strip() or "schematic"
            input_db_extra.append(f'-schematic_view "{q(schematic_view)}"')
        output_db_extra.extend([cdl_map_directory, f'-view_name "{q(target.view)}"'])
        output_db_extra.extend(baseline.mode_options("output_db", qrc_output_type))
    else:
        qrc_output_type = output_type

    if netlist_output and cdl_map_directory:
        output_db_extra.append(cdl_map_directory)

    output_name_space = ctx.name_source.upper()
    if not netlist_output:
        output_name_space = "SCHEMATIC"

    tech_dirs = extract_tech_dirs(cfg)
    tech_dir = str(tech_dirs[0])
    technology_options = qrc_technology_options(cfg, ctx, tech_dirs, baseline)
    technology_options.extend(qrc_finfet_technology(cfg, ctx, tech_dirs))
    full_extract, net_extract = qrc_extract_commands(cfg, ctx, rc_type, baseline)
    cell_options = qrc_cell_options(cfg, ctx, Path(tech_dir))
    pin_control = ""
    if cfg.flag("netlist", "pin_order_enable"):
        if netlist_output and output_name_space == "LAYOUT":
            raise ValueError(
                "QRC User Pin Order requires the schematic output namespace "
                "for file netlists; set extract.name_source to 'schematic' or "
                "disable netlist.pin_order_enable"
            )
        pin_control = f'-pin_order_file "{q(qrc_pin_order_file(cfg, ctx))}"'

    output_hierarchy_delimiter = ""
    if cfg.flag("netlist", "hierarchy_delimiter_enable"):
        delimiter = cfg.text(
            "netlist",
            "hierarchy_delimiter",
        )
        if (
            output_type.strip().casefold() == "spef"
            and delimiter not in QRC_SPEF_HIERARCHY_DELIMITERS
        ):
            supported = "', '".join(sorted(QRC_SPEF_HIERARCHY_DELIMITERS))
            raise ValueError(
                f"Unsupported QRC SPEF hierarchy delimiter {delimiter!r}; "
                f"use one of: '{supported}'"
            )
        output_hierarchy_delimiter = f'-hierarchy_delimiter "{q(delimiter)}"'

    parasitic_info_options = qrc_parasitic_info_options(cfg, output_type)
    disable_instances = ""
    if output_type == "dspf":
        remove_instances = cfg.text(
            "netlist",
            "dspf_remove_instances",
        ) == "TRUE"
        disable_instances = (
            f"-disable_instances {'true' if remove_instances else 'false'}"
        )
    output_multi_value_rc = ""
    if netlist_output and qrc_per_corner_output(cfg, output_type):
        # Quantus defaults to one multi-value netlist; per-corner files need false.
        output_multi_value_rc = "-output_multi_value_rc false"
    filter_c = qrc_filter_cap(cfg)
    filter_r = qrc_filter_res(cfg)
    floating_net_option = qrc_floating_net_option(cfg)
    lvs_data = "pvs" if cfg.lvs_tool.upper() == "PVS" else "calibre"

    input_db_options = [
        f"-type {lvs_data}",
        f'-run_name "{q(ctx.layout_cell)}"',
        f'-directory_name "{q(ctx.cci_dir)}"',
        f'-layer_map_file "{q(ctx.cci_dir)}/{q(ctx.layout_cell)}.gds.map"',
        *input_db_extra,
    ]
    if finfet:
        input_db_options.append(
            f'-device_properties_file "{q(str(Path(ctx.cci_dir) / ctx.layout_cell))}.pdsp"'
        )
    output_defaults = baseline.options("output_db", "file" if netlist_output else "")
    if output_hierarchy_delimiter:
        output_defaults = [
            line for line in output_defaults
            if line.split()[0] != "-hierarchy_delimiter"
        ]
    output_db_options = [
        f"-type {qrc_output_type}",
        *output_defaults,
        output_hierarchy_delimiter if netlist_output else "",
        *parasitic_info_options,
        disable_instances,
        output_multi_value_rc,
        pin_control,
        *output_db_extra,
    ]
    lines = qrc_lines(
        cfg,
        ctx,
        technology_options,
        input_db_options,
        output_db_options,
        output_file,
        filter_c,
        filter_r,
        floating_net_option,
        output_name_space,
        cell_options,
        baseline,
    )
    if qrc_output_type == "idspf":
        lines.extend(["", "parasitic_reduction \\", "    -enable_reduction false"])
    for command in (full_extract, net_extract):
        if command:
            lines.append("")
            lines.append(command)
    text = clean_lines(lines)
    return write_text(ctx.log_dir / "qrc.ccl", qrc_path_text(ctx, text) if finfet else text)


RC_TYPE_QRC = {
    "RCC": "rc_coupled",
    "RC": "rc_decoupled",
    "R+Cg+Cc": "rc_coupled",
    "R+Cg": "rc_decoupled",
    "R": "r_only",
    "CC": "c_only_coupled",
    "Cg+Cc": "c_only_coupled",
    "C": "c_only_decoupled",
    "Cg": "c_only_decoupled",
    "NONE": "none",
    "No-RC": "none",
}


QRC_OUTPUT_FORMAT_ALIASES = {
    "spice": "sp",
    "hspice": "sp",
    "idspfview": "idspf",
    "i-dspf": "idspf",
    "interactive_dspf": "idspf",
}


QRC_FILE_OUTPUT_TYPES = {
    "sp": "spice",
    "dspf": "dspf",
    "spef": "spef",
}


QRC_SPEF_HIERARCHY_DELIMITERS = {"/", ".", "|", ":"}
