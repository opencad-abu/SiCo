"""Serialize the RCE run manifest from current run facts and generated commands."""

from __future__ import annotations

from pathlib import Path

from .config import DesignContext, RceConfig
from .reduction import reduced_output_paths, reduced_view_name, reduction_enabled
from .textutil import write_text


def write_run_manifest(
    cfg: RceConfig, ctx: DesignContext, generated: dict[str, Path],
    *, config_path: Path | None = None,
) -> None:
    lines = [
        f"config = {config_path or cfg.config_path}",
        f"run_dir = {ctx.run_dir}",
        f"input_type = {ctx.input_type}",
        f"top_cell_source = {ctx.top_cell_source}",
        f"name_source = {ctx.name_source}",
        f"top_cell = {ctx.top_cell}",
        f"source_cell = {ctx.source_cell}",
        f"layout_cell = {ctx.layout_cell}",
        f"lvs_tool = {cfg.lvs_tool}",
        f"extract_tool = {cfg.ext_tool}",
        f"corners = {', '.join(cfg.corners)}",
        f"corner_temperatures = {', '.join(cfg.corner_temperatures())}",
        f"start_rve = {cfg.start_rve}",
        f"output_path = {cfg.output_path(ctx)}",
        f"output_paths = {', '.join(str(path) for path in cfg.output_paths(ctx))}",
        f"reduction_enabled = {reduction_enabled(cfg) and not (cfg.is_multi_corner or cfg.is_multi_corner_scope)}",
        "reduced_output_paths = "
        f"{', '.join(str(path) for path in reduced_output_paths(cfg, ctx))}",
        "reduced_view = "
        f"{reduced_view_name(cfg, ctx) if reduction_enabled(cfg) and not (cfg.is_multi_corner or cfg.is_multi_corner_scope) and cfg.is_view_output else ''}",
        "",
        "[generated]",
    ]
    for name, path in sorted(generated.items()):
        lines.append(f"{name} = {path}")
    write_text(ctx.log_dir / "rce.manifest", "\n".join(lines) + "\n")
