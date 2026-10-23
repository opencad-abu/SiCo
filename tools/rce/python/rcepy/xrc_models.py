"""Immutable settings, stage specifications and plans for Calibre PEX."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class XrcSettings:
    control_file: Path
    lvs_rule_file: Path
    rule_file: Path
    hcell_file: Path | None
    xcell_file: Path | None
    base_hcell_file: Path | None
    base_xcell_file: Path | None
    blocked_cells: tuple[str, ...]
    layout_netlist: Path
    lvs_report: Path
    output_file: Path
    expected_output_files: tuple[Path, ...]
    formatter_corners: tuple[str, ...]
    corner_name: str
    temperature: str
    netlist_format: str
    pdb_arguments: tuple[str, ...] | None
    fmt_switches: tuple[str, ...]
    capacitance_only: bool
    selection_switch: str | None
    pin_order_line: str | None
    character_map_line: str | None
    bus_delimiter: str | None

    @property
    def simple_netlist(self) -> bool:
        """Return whether this extraction produces a parasitic-free netlist."""
        return self.pdb_arguments is None


@dataclass(frozen=True)
class XrcStageSpec:
    name: str
    command: list[str]
    log_file: Path
    control_file: Path
    expected_paths: tuple[Path, ...]
    check: str


@dataclass(frozen=True)
class XrcPlan:
    settings: tuple[XrcSettings, ...]
    native_multi_corner: bool
