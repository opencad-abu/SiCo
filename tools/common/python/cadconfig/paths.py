"""Configured path vocabulary and explicit-base filesystem resolution."""

from __future__ import annotations

import os
from pathlib import Path
import re


PATH_KEYS = frozenset("""
run.root run.cds_lib input.schematic.cdl_header_file input.layout.layer_map
input.cdl.file input.cdl.run_directory input.gds.file input.svdb.dir input.cci.dir
drc.runset_file lvs.runset_file lvs.hcell_file extract.tech_dir
extract.view.cellmap_file extract.view.device_mapping_file
extract.view.layer_mapping_file reduction.selection_file
reduction.canonical_device_file netlist.output_path netlist.pin_order_file
abstract.options_file input.cell_list_file output.lef_file
""".split())
SELECTION_PATH_KEYS = frozenset({"selection.nets", "selection.cells"})
_ENV_REF = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def resolve_path(value: str, base: Path, *, resolve_symlinks: bool = True) -> Path:
    """Resolve from the adapter's explicit base, rejecting unresolved variables."""
    if not isinstance(value, str):
        raise ValueError("Configured path must be a string")
    text = value.strip()
    if not text:
        raise ValueError("Cannot resolve an empty path")

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if not os.environ.get(name):
            raise ValueError(
                f"Undefined or empty environment variable {match.group(0)} in path {text!r}"
            )
        return os.environ[name]

    path = Path(_ENV_REF.sub(replace, text)).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve() if resolve_symlinks else Path(os.path.abspath(path))
