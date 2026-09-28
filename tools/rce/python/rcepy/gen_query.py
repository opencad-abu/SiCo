"""Calibre query command generation."""

from __future__ import annotations

from pathlib import Path

from caddefaults import Defaults, resolve_defaults
from .config import DesignContext, RceConfig
from .qrc_defaults import QrcDefaults
from .textutil import clean_lines, write_text


def generate_query(cfg: RceConfig, ctx: DesignContext, *, defaults: Defaults | None = None) -> Path:
    tool = cfg.ext_tool.upper()
    if tool in {"STARRC", "STARXTRACT"}:
        return generate_star_query(ctx)
    baseline = QrcDefaults(resolve_defaults(("quantus.options",), defaults, ctx.log_dir))
    return _generate_qrc_query(ctx, baseline)


def _generate_qrc_query(ctx: DesignContext, baseline: QrcDefaults) -> Path:
    prefix = f"{ctx.cci_dir}/{ctx.layout_cell}"
    lines = [
        "echo",
        "status",
        *_qrc_query_properties(baseline),
        f"response file {prefix}.gds.map",
        "gds seed property device original",
        "gds map",
        "response direct",
        f"gds write {prefix}.agf",
        "layout netlist trivial pins YES",
        "layout netlist empty cells YES",
        "layout netlist names NONE",
        "layout netlist primitive device subckts NO",
        "layout netlist pin locations YES",
        "layout netlist hierarchy AGF",
        f"layout nametable write {prefix}.lnn EXPAND_CELLS",
        "layout netlist device templates YES",
        f"layout netlist write {prefix}_pin_xy.spi",
        "layout netlist separated properties YES",
        f"layout separated properties write {prefix}.props",
        f"source hierarchy write {prefix}.sph",
        f"layout hierarchy write {prefix}.lph",
        f"net xref write {prefix}.nxf BOX LNXF",
        f"instance xref write {prefix}.ixf",
        f"port table write {prefix}.ports",
        f"port table cells write {prefix}.ports_cells",
        f"response file {prefix}.devtab",
        "device table",
        "response direct",
        f"lvs settings report write {prefix}.lvs_settings",
        "terminate",
    ]
    return write_text(ctx.log_dir / "query.cal", clean_lines(lines))


def _qrc_query_properties(baseline: QrcDefaults) -> list[str]:
    lines = []
    for option, command in (
        ("net_property_value", "netprop"),
        ("instance_property_value", "placeprop"),
        ("device_property_value", "devprop"),
    ):
        value = baseline.value("input_db", option)
        if value is not None:
            lines.append(f"gds {command} number {value}")
    return lines


def generate_star_query(
    ctx: DesignContext,
    output_path: Path | None = None,
) -> Path:
    """Write the Calibre query command consumed by StarRC.

    The command file is generated in the run log by default.  Its output
    prefix deliberately remains in ``ctx.cci_dir`` so the same command works
    for both SVDB inputs (where Calibre query creates the CCI data) and CCI
    inputs (where that data already exists externally).
    """
    prefix = f"{ctx.cci_dir}/{ctx.layout_cell}"
    lines = [
        "GDS NETPROP NUMBER 5",
        "GDS PLACEPROP NUMBER 6",
        "GDS DEVPROP NUMBER 7",
        "GDS SEED PROPERTY ORIGINAL",
        "LAYOUT NETLIST SEPARATED PROPERTIES YES",
        f"RESPONSE FILE {prefix}.gds.map",
        "GDS MAP",
        "RESPONSE DIRECT",
        f"GDS WRITE {prefix}.agf",
        f"RESPONSE FILE {prefix}.devtab",
        "DEVICE TABLE",
        "RESPONSE DIRECT",
        "LAYOUT NETLIST TRIVIAL PINS YES",
        "LAYOUT NETLIST EMPTY CELLS YES",
        "LAYOUT NETLIST NAMES NONE",
        f"LAYOUT NAMETABLE WRITE {prefix}.lnn",
        "LAYOUT NETLIST PRIMITIVE DEVICE SUBCKTS YES",
        "LAYOUT NETLIST PIN LOCATIONS YES",
        "LAYOUT NETLIST HIERARCHY AGF",
        "LAYOUT NETLIST DEVICE LOCATION CENTER",
        f"LAYOUT NETLIST WRITE {prefix}.nl",
        f"NET XREF WRITE {prefix}.nxf",
        f"INSTANCE XREF WRITE {prefix}.ixf",
        f"PORT TABLE CELLS WRITE {prefix}.ports_cells",
        f"CELL EXTENTS WRITE {prefix}.extents",
        "TERMINATE",
    ]
    return write_text(
        output_path if output_path is not None else ctx.log_dir / "star.query.cmd",
        clean_lines(lines),
    )


# Keep the old private spelling available to downstream integrations that may
# have imported it before the public helper was introduced.
_generate_star_query = generate_star_query
