"""Compatibility wrapper for shared GDS command generation."""

from __future__ import annotations

from pathlib import Path

from caddefaults import Defaults

from cadstage.command_files import GdsCommandOptions, write_streamout_cmd

from .config import DesignContext, RceConfig


def generate_gds(cfg: RceConfig, ctx: DesignContext, *, defaults: Defaults | None = None) -> Path:
    layer_map = cfg.path("input", "layout", "layer_map")
    view = cfg.text("input", "layout", "view", default="layout")
    return write_streamout_cmd(
        ctx.gds_dir / "streamout.cmd",
        GdsCommandOptions(
            layout_lib=cfg.text("input", "layout", "lib"),
            layout_cell=ctx.layout_cell,
            layout_view=view,
            layer_map=layer_map,
            output_filename=Path(ctx.layout_path).name,
            replace_bus_bit_char=cfg.flag(
                "options", "replace_bus_bit_char", default=True
            ),
        ),
        defaults=defaults,
    )
