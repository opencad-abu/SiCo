"""Rendering for command files shared by CDL and GDS stages."""

from __future__ import annotations

from caddefaults import Defaults, resolve_defaults
from .defaults import cdl_lines, streamout_lines

from dataclasses import dataclass
from pathlib import Path


def _quote(value: str) -> str:
    """Escape a value for the double-quoted fields used by Cadence tools."""
    return str(value).replace('"', '\\"')


@dataclass(frozen=True)
class CdlCommandOptions:
    schematic_lib: str
    schematic_cell: str
    schematic_view: str
    source_filename: str
    header_file: str
    replace_angle_brackets: bool = True


@dataclass(frozen=True)
class GdsCommandOptions:
    layout_lib: str
    layout_cell: str
    layout_view: str
    layer_map: str
    output_filename: str
    replace_bus_bit_char: bool = True


def render_cdl_env(options: CdlCommandOptions, *, defaults: Defaults | None = None) -> str:
    """Render the ``si -batch`` environment file."""
    text = cdl_lines(resolve_defaults(("cdl.env",), defaults).source("cdl.env"))
    return f"""simLibName                = \"{_quote(options.schematic_lib)}\"
simCellName               = \"{_quote(options.schematic_cell)}\"
simViewName               = \"{_quote(options.schematic_view)}\"
simSimulator              = \"auCdl\"
{text.get("netlisting", "")}
hnlNetlistFileName        = \"{_quote(options.source_filename)}\"
{text.get("devices", "")}
incFILE                   = \"{_quote(options.header_file)}\"
auCdlReplaceAngleBracketsWithSquare = {"'t" if options.replace_angle_brackets else "'nil"}
"""


def render_streamout_cmd(options: GdsCommandOptions, *, defaults: Defaults | None = None) -> str:
    """Render the ``strmout`` command file."""
    text = streamout_lines(resolve_defaults(("streamout.options",), defaults).source("streamout.options"))
    return f"""library \"{_quote(options.layout_lib)}\"
topCell \"{_quote(options.layout_cell)}\"
view \"{_quote(options.layout_view)}\"
layerMap \"{_quote(options.layer_map)}\"
strmFile \"{_quote(options.output_filename)}\"
{text.get("geometry", "")}
logFile                            "strmOut.log"
{text.get("vertices", "")}
outputDir                          "."
{text.get("pins", "")}
replaceBusBitChar                  "{"true" if options.replace_bus_bit_char else "false"}"
runDir                             "."
{text.get("format", "")}
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_cdl_env(path: Path, options: CdlCommandOptions, *, defaults: Defaults | None = None) -> Path:
    """Render and write an ``si.env`` file."""
    defaults = resolve_defaults(("cdl.env",), defaults, path.parent)
    return _write(path, render_cdl_env(options, defaults=defaults))


def write_streamout_cmd(path: Path, options: GdsCommandOptions, *, defaults: Defaults | None = None) -> Path:
    """Render and write a ``streamout.cmd`` file."""
    defaults = resolve_defaults(("streamout.options",), defaults, path.parent)
    return _write(path, render_streamout_cmd(options, defaults=defaults))
