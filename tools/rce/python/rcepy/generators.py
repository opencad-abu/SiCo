"""Command file generator facade for RCE tool stages."""

from __future__ import annotations

from pathlib import Path

from caddefaults import Defaults, resolve_defaults
from .default_files import default_files
from .qrc_defaults import qrc_launch_arguments

from .config import DesignContext, RceConfig
from .gen_cdl import generate_cdl
from .gen_gds import generate_gds
from .gen_lvs import generate_lvs
from .gen_qrc import generate_qrc
from .gen_query import generate_query, generate_star_query
from .gen_starrc import generate_starrc
from .gen_xrc import generate_xrc
from .netlist_postprocess import configured_bus_delimiter_mapping
from .textutil import ensure_dirs


def generate_all(cfg: RceConfig, ctx: DesignContext, *, defaults: Defaults | None = None) -> dict[str, Path]:
    defaults = resolve_defaults(default_files(cfg), defaults)
    configured_bus_delimiter_mapping(cfg)
    ensure_dirs(ctx.log_dir, ctx.cdl_dir, ctx.gds_dir, ctx.db_dir)
    defaults.save(ctx.log_dir)
    if cfg.ext_tool.upper() in {"QRC", "QUANTUS"}:
        qrc_launch_arguments(defaults)
    outputs: dict[str, Path] = {}
    # CCI inputs already contain the Calibre database, so they intentionally
    # skip the executable ``query`` stage.  StarRC still consumes a query
    # command file; generate and publish it before the extract command.
    if (
        ctx.input_type == "CCI"
        and cfg.ext_tool.upper() in {"STARRC", "STARXTRACT"}
    ):
        outputs["query"] = generate_star_query(ctx)
    for stage in cfg.enabled_stages():
        if stage == "cdl":
            outputs["cdl"] = generate_cdl(cfg, ctx, defaults=defaults)
        elif stage == "gds":
            outputs["gds"] = generate_gds(cfg, ctx, defaults=defaults)
        elif stage == "lvs":
            outputs["lvs"] = generate_lvs(cfg, ctx, defaults=defaults)
        elif stage == "query":
            outputs["query"] = generate_query(cfg, ctx, defaults=defaults)
        elif stage == "extract":
            outputs["extract"] = _generate_extract(cfg, ctx, defaults=defaults)
    return outputs


def _generate_extract(cfg: RceConfig, ctx: DesignContext, *, defaults: Defaults) -> Path:
    tool = cfg.ext_tool.upper()
    if tool in {"QRC", "QUANTUS"}:
        return generate_qrc(cfg, ctx, defaults=defaults)
    if tool in {"STARRC", "STARXTRACT"}:
        return generate_starrc(cfg, ctx, defaults=defaults)
    if tool in {"CALXRC", "XRC", "CALIBRE-XRC", "CALIBRE_XRC"}:
        return generate_xrc(cfg, ctx, defaults=defaults)
    raise ValueError(f"Unsupported extraction tool: {cfg.ext_tool}")
