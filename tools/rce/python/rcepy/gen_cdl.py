"""Compatibility wrapper for shared CDL command generation."""

from __future__ import annotations

from pathlib import Path

from caddefaults import Defaults

from cadstage.command_files import CdlCommandOptions, write_cdl_env

from .config import DesignContext, RceConfig


def generate_cdl(cfg: RceConfig, ctx: DesignContext, *, defaults: Defaults | None = None) -> Path:
    header = cfg.path(
        "input", "schematic", "cdl_header_file"
    )
    return write_cdl_env(
        ctx.cdl_dir / "si.env",
        CdlCommandOptions(
            schematic_lib=cfg.text("input", "schematic", "lib"),
            schematic_cell=cfg.text("input", "schematic", "cell"),
            schematic_view=cfg.text("input", "schematic", "view"),
            source_filename=Path(ctx.source_path).name,
            header_file=header,
            replace_angle_brackets=cfg.flag(
                "options", "replace_bus_bit_char", default=True
            ),
        ),
        defaults=defaults,
    )
