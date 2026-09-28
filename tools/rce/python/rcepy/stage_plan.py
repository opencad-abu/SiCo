"""Build the ordered command stages for one RCE request."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from caddefaults import Defaults
from .qrc_defaults import qrc_launch_arguments

from .config import DesignContext, RceConfig
from .gen_lvs import lvs_hcell_arguments
from .stage_runner import Stage
from .xrc import build_xrc_stage_specs


PublicationResolver = Callable[[], tuple[tuple[Path, Path], ...]]


def build_stages(
    cfg: RceConfig,
    ctx: DesignContext,
    publications: PublicationResolver,
    *, defaults: Defaults | None = None,
) -> list[Stage]:
    """Return stages in execution order without retaining runner state."""

    stages: list[Stage] = []
    enabled = cfg.enabled_stages()
    if "cdl" in enabled:
        stages.append(
            Stage(
                "cdl",
                ["si", "-batch"],
                ctx.cdl_dir,
                ctx.log_dir / "si.log",
                ctx.cdl_dir / "si.env",
            )
        )
    if "gds" in enabled:
        command = ["strmout", "-templateFile", "streamout.cmd"]
        if cfg.flag("options", "replace_bus_bit_char", default=True):
            command.append("-replaceBusBitChar")
        stages.append(
            Stage(
                "gds",
                command,
                ctx.gds_dir,
                ctx.log_dir / "strmout.log",
                ctx.gds_dir / "streamout.cmd",
            )
        )
    if "lvs" in enabled:
        stages.append(
            Stage(
                "lvs",
                [
                    "calibre",
                    "-lvs",
                    "-turbo",
                    cfg.lvs_cpus,
                    "-hier",
                    *lvs_hcell_arguments(cfg),
                    str(ctx.log_dir / "lvs.cal"),
                ],
                ctx.run_dir,
                ctx.log_dir / "callvs.log",
                ctx.log_dir / "lvs.cal",
                check="lvs",
            )
        )
    if "query" in enabled:
        stages.append(_query_stage(cfg, ctx))
    if "extract" in enabled:
        if cfg.is_xrc:
            stages.extend(_xrc_stages(cfg, ctx))
        else:
            stages.append(build_extract_stage(cfg, ctx, publications, defaults=defaults))
    return stages


def build_extract_stage(
    cfg: RceConfig,
    ctx: DesignContext,
    publications: PublicationResolver,
    *, defaults: Defaults | None = None,
) -> Stage:
    """Build the single QRC or StarRC extraction stage."""

    expected_paths = _netlist_output_paths(cfg, publications)
    tool = cfg.ext_tool.upper()
    if tool in {"QRC", "QUANTUS"}:
        return Stage(
            "extract",
            ["qrc", *qrc_launch_arguments(defaults), "-cmd", str(ctx.log_dir / "qrc.ccl")],
            ctx.run_dir,
            ctx.log_dir / "qrc.stdout.log",
            ctx.log_dir / "qrc.ccl",
            expected_paths=expected_paths,
            require_nonempty_outputs=bool(expected_paths),
        )
    if tool in {"STARRC", "STARXTRACT"}:
        return Stage(
            "extract",
            ["StarXtract", str(ctx.log_dir / "star.cmd")],
            ctx.run_dir,
            ctx.log_dir / "starrc.log",
            ctx.log_dir / "star.cmd",
            expected_paths=expected_paths,
            require_nonempty_outputs=bool(expected_paths),
        )
    raise ValueError(f"Unsupported extraction tool: {cfg.ext_tool}")


def _query_stage(cfg: RceConfig, ctx: DesignContext) -> Stage:
    query_file = ctx.log_dir / "star.query.cmd"
    query_cmd = ["calibre", "-query", ctx.svdb_dir]
    if cfg.ext_tool.upper() not in {"STARRC", "STARXTRACT"}:
        query_file = ctx.log_dir / "query.cal"
        query_cmd = [
            "calibre",
            "-query",
            ctx.svdb_dir,
            ctx.layout_cell,
            "-query_input",
            str(query_file),
        ]
    stdin_file = query_file if cfg.ext_tool.upper() in {"STARRC", "STARXTRACT"} else None
    return Stage(
        "query",
        query_cmd,
        ctx.run_dir,
        ctx.log_dir / "query.log",
        query_file,
        stdin_file=stdin_file,
    )


def _xrc_stages(cfg: RceConfig, ctx: DesignContext) -> list[Stage]:
    return [
        Stage(
            spec.name,
            spec.command,
            ctx.run_dir,
            spec.log_file,
            spec.control_file,
            check=spec.check,
            expected_paths=spec.expected_paths,
            require_nonempty_outputs=(
                spec.name.startswith("xrc_fmt") and bool(spec.expected_paths)
            ),
        )
        for spec in build_xrc_stage_specs(cfg, ctx)
    ]


def _netlist_output_paths(
    cfg: RceConfig, publications: PublicationResolver
) -> tuple[Path, ...]:
    if cfg.is_view_output:
        return ()
    return tuple(native for native, _ in publications())


__all__ = ["build_extract_stage", "build_stages"]
