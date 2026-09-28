"""Source OA identity and session-bound target selection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from .errors import RequestValidationError
from .model_validation import _path, validate_oa_name


@dataclass(frozen=True)
class SourceDesign:
    cds_lib: Path
    library: str
    cell: str
    view: str = "schematic"
    startup_file: Optional[Path] = None
    simrc: Optional[Path] = None

    def validate(self) -> "SourceDesign":
        cds_lib = _path(self.cds_lib, "source cds.lib", file=True)
        startup = None if self.startup_file is None else _path(self.startup_file, "source startup file", file=True)
        simrc = None if self.simrc is None else _path(self.simrc, "source simrc", file=True)
        return SourceDesign(
            cds_lib,
            validate_oa_name(self.library, "source library"),
            validate_oa_name(self.cell, "source cell"),
            validate_oa_name(self.view, "source view"),
            startup,
            simrc,
        )


@dataclass(frozen=True)
class TargetSelection:
    """Target is session-bound; the request stores names, never a target cds.lib."""

    library: Optional[str] = None
    cell: Optional[str] = None
    generate_symbol_view: bool = False
    generate_netlist_view: bool = False
    overwrite: str = "reject"
    overwrite_symbol_view: bool = False
    overwrite_netlist_view: bool = False

    def validate(self, source_cell: str) -> "TargetSelection":
        library = None if self.library in (None, "") else validate_oa_name(self.library, "target library")
        cell = None if self.cell in (None, "") else validate_oa_name(self.cell, "target cell")
        if self.overwrite != "reject":
            raise RequestValidationError("only overwrite='reject' is supported in v1")
        overwrite_symbol = bool(self.overwrite_symbol_view)
        overwrite_netlist = bool(self.overwrite_netlist_view)
        if overwrite_symbol and not self.generate_symbol_view:
            raise RequestValidationError(
                "overwrite_symbol_view requires generate_symbol_view"
            )
        if overwrite_netlist and not self.generate_netlist_view:
            raise RequestValidationError(
                "overwrite_netlist_view requires generate_netlist_view"
            )
        return TargetSelection(
            library,
            cell or source_cell,
            bool(self.generate_symbol_view),
            bool(self.generate_netlist_view),
            "reject",
            overwrite_symbol,
            overwrite_netlist,
        )
