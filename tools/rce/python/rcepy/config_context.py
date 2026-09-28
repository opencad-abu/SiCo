"""Resolved design identity and workspace paths for one flow request."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DesignContext:
    input_type: str
    run_dir: Path
    log_dir: Path
    db_dir: Path
    cdl_dir: Path
    gds_dir: Path
    top_cell_source: str
    name_source: str
    top_cell: str
    source_cell: str
    source_path: str
    layout_cell: str
    layout_path: str
    svdb_dir: str
    cci_dir: str
    pin_order_source: str
