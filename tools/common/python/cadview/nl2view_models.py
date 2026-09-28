"""Immutable request and format values for Cadence text-view imports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional


@dataclass(frozen=True)
class FormatSpec:
    """The cdsTextTo5x language and conventional view for a netlist format."""

    language: str
    default_view: str


FORMAT_SPECS: Mapping[str, FormatSpec] = {
    "spectre": FormatSpec(language="spectre", default_view="spectreText"),
    # IC23.10 accepts this undocumented token and selects its HSPICE parser.
    "hspice": FormatSpec(language="hspice", default_view="hspiceText"),
    "spice": FormatSpec(language="spice", default_view="spiceText"),
    "dspf": FormatSpec(language="dspf", default_view="dspfText"),
}


@dataclass(frozen=True)
class ImportRequest:
    """A validated cdsTextTo5x import request."""

    source: Path
    netlist_format: str
    library: str
    cell: str
    view: str
    cds_library_file: Path
    log_file: Optional[Path] = None
    copy_source: bool = True
    executable: str = "cdsTextTo5x"
    cds_lib_debug: Optional[str] = None
    expected_library_path: Optional[Path] = None

    @property
    def format_spec(self) -> FormatSpec:
        return FORMAT_SPECS[self.netlist_format]

    def command(self) -> List[str]:
        command = [
            self.executable,
            "-LIB",
            self.library,
            "-CELL",
            self.cell,
            "-VIEW",
            self.view,
        ]
        command.extend(("-CDSLIB", str(self.cds_library_file)))
        command.extend(("-LANG", self.format_spec.language))
        if self.log_file:
            command.extend(("-LOG", str(self.log_file)))
        command.append(str(self.source))
        return command
