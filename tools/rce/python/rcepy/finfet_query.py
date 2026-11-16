"""Calibre query export settings for multigate extraction databases."""

from __future__ import annotations

from pathlib import Path

from .command_paths import command_path
from .config_context import DesignContext
from .textutil import clean_lines, write_text


def finfet_query_prefix(ctx: DesignContext) -> str:
    """Return an unquoted absolute prefix shared by Calibre and its consumers."""
    if any(char.isspace() for char in str(ctx.run_dir)):
        raise ValueError("FinFET Calibre query requires a run directory without whitespace")
    if any(char.isspace() for char in ctx.layout_cell):
        raise ValueError("FinFET Calibre query requires a layout cell name without whitespace")
    prefix = str(Path(ctx.cci_dir) / ctx.layout_cell)
    if any(char in prefix for char in '"\'\\'):
        raise ValueError("FinFET Calibre query paths must not contain quotes or backslashes")
    # Calibre Query treats quote characters as filename bytes, not delimiters.
    # StarRC reads these same paths relative to the command file's directory,
    # so keep them absolute even though the query runs from ctx.run_dir.
    return command_path(ctx, prefix, "query")


def generate_finfet_star_query(ctx: DesignContext, output_path: Path | None = None) -> Path:
    prefix = finfet_query_prefix(ctx)

    def output(suffix: str) -> str:
        return prefix + suffix

    lines = [
        "GDS NETPROP NUMBER 5", "GDS PLACEPROP NUMBER 6", "GDS DEVPROP NUMBER 7",
        "GDS SEED PROPERTY DEVICE ORIGINAL",
        f"RESPONSE FILE {output('.gds.map')}", "GDS MAP", "RESPONSE DIRECT",
        f"GDS WRITE {output('.agf')}", f"RESPONSE FILE {output('.devtab')}",
        "DEVICE TABLE", "RESPONSE DIRECT", "LAYOUT NETLIST DEVICE LOCATION CENTER",
        "LAYOUT NETLIST TRIVIAL PINS YES", "LAYOUT NETLIST EMPTY CELLS YES",
        "LAYOUT NETLIST PRIMITIVE DEVICE SUBCKTS NO",
        "LAYOUT NETLIST SEPARATED PROPERTIES NO", "LAYOUT NETLIST PIN LOCATIONS NO",
        "LAYOUT NETLIST DEVICE TEMPLATES YES", "LAYOUT NETLIST HIERARCHY AGF",
        "LAYOUT NETLIST NAMES NONE",
        f"LAYOUT NAMETABLE WRITE {output('.lnn')} EXPAND_CELLS",
        f"LAYOUT NETLIST WRITE {output('.nl')}",
        f"LAYOUT SEPARATED PROPERTIES WRITE {output('.pdsp')}",
        f"NET XREF WRITE {output('.nxf')} BOX LNXF",
        f"INSTANCE XREF WRITE {output('.ixf')}",
        f"CELL EXTENTS WRITE {output('.extents')}",
        f"PORT TABLE CELLS WRITE {output('.ports_cells')}",
        f"LVS SETTINGS REPORT WRITE {output('.lvs_settings')}", "TERMINATE",
    ]
    return write_text(output_path or ctx.log_dir / "star.query.cmd", clean_lines(lines))


def qrc_finfet_query_lines(lines: list[str], prefix: str) -> list[str]:
    """Enable separate properties before QRC pin export and align filenames."""
    result = []
    separated = "layout netlist separated properties YES"
    for line in lines:
        if line == separated:
            continue
        if line == "layout netlist pin locations YES":
            # Required when the LVS deck pushes separate device properties.
            result.append(separated)
        result.append(line.replace(prefix + ".props", prefix + ".pdsp"))
    return result
