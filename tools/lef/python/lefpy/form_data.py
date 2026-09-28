"""Emit validated LEF configuration as inert SKILL form data."""

from __future__ import annotations

import os
from pathlib import Path

from .config import LefConfig
from .replay import skill_string


def _skill_bool(value: bool) -> str:
    return "t" if value else "nil"


def _skill_list(values: list[str]) -> str:
    return f"list({' '.join(values)})"


def _entry(name: str, value: str) -> str:
    return f"  list({skill_string(name)} {value})"


def render_form_data(cfg: LefConfig) -> str:
    cells = _skill_list([skill_string(cell) for cell in cfg.cells])
    bin_options = _skill_list(
        [
            _skill_list([skill_string(name), skill_string(value)])
            for name, value in cfg.bin_options
        ]
    )
    strings = {
        "config_path": str(cfg.config_path),
        "run_root": str(cfg.run_dir.parent),
        "run_dir": str(cfg.run_dir),
        "cds_lib": str(cfg.cds_lib),
        "executable": cfg.abstract_executable,
        "library": cfg.library,
        "cells_display": ", ".join(cfg.cells),
        "source_cell_list": str(cfg.source_cell_list or ""),
        "layout_view": cfg.layout_view,
        "logical_view": cfg.logical_view,
        "abstract_view": cfg.abstract_view,
        "options_file": str(cfg.options_file or ""),
        "bin_name": cfg.bin_name,
        "output_lef": str(cfg.output_lef),
        "lef_version": cfg.lef_version,
        "run_type": cfg.run_type,
        "queue_name": cfg.queue_name,
        "server_name": cfg.server_name,
        "cpus": cfg.cpus,
    }
    entries = [_entry(name, skill_string(value)) for name, value in strings.items()]
    entries.extend(
        (
            _entry("cells", cells),
            _entry("export_geometry", _skill_bool(cfg.export_geometry)),
            _entry("export_technology", _skill_bool(cfg.export_technology)),
            _entry("run_pins", _skill_bool(cfg.run_pins)),
            _entry("run_extract", _skill_bool(cfg.run_extract)),
            _entry("run_abstract", _skill_bool(cfg.run_abstract)),
            _entry("bin_options", bin_options),
        )
    )
    return "lefSetFormLoadData(list(\n" + "\n".join(entries) + "\n))\n"


def write_form_data(cfg: LefConfig, path: str | Path) -> Path:
    output = Path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(output, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(render_form_data(cfg))
    return output
