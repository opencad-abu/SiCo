"""Plan Calibre XRC stages and output publications."""

from __future__ import annotations

import re
from pathlib import Path

from .config import DesignContext, RceConfig

# Compatibility: retain former public imports as the identical owner objects.
# Remove when supported consumers have migrated, at the next breaking API version.
from .xrc_cells import write_xrc_cell_lists as write_xrc_cell_lists
from .xrc_models import XrcPlan, XrcSettings, XrcStageSpec
from .xrc_options import xrc_mode_switches
from .xrc_options import xrc_netlist_options as xrc_netlist_options
from .xrc_options import xrc_option_lines as xrc_option_lines
from .xrc_paths import calibre_executable, xrc_corner_dir
from .xrc_settings import load_xrc_settings


def build_xrc_plan(cfg: RceConfig, ctx: DesignContext) -> XrcPlan:
    if not cfg.is_multi_corner:
        return XrcPlan((load_xrc_settings(cfg, ctx),), False)
    if cfg.is_view_output:
        raise ValueError(
            "Calibre XRC multi-corner extraction currently requires a file netlist, "
            "not a Calibre View"
        )

    published = cfg.output_paths(ctx)
    temperatures = cfg.corner_temperatures()
    simple_netlist = xrc_mode_switches(cfg)[0] is None
    rule_raw = cfg.text("extract", "xrc", "rule_file") or cfg.text(
        "extract", "xrc", "deck_file"
    )
    if rule_raw and len(set(temperatures)) <= 1 and not simple_netlist:
        first_corner = cfg.corners[0]
        first_dir = xrc_corner_dir(cfg, first_corner)
        shared_rule = cfg.resolve_path(rule_raw, base=first_dir)
        base = Path(cfg.output_path(ctx)).with_suffix("")
        native = tuple(
            base.with_name(f"{base.name}_{corner}") for corner in cfg.corners
        )
        settings = load_xrc_settings(
            cfg,
            ctx,
            corner_name=first_corner,
            output_file=base,
            expected_output_files=native,
            formatter_corners=cfg.corners,
            rule_file_override=shared_rule,
            temperature=temperatures[0] if temperatures else None,
        )
        return XrcPlan((settings,), True)

    settings_list: list[XrcSettings] = []
    shared_rule: Path | None = None
    if rule_raw:
        shared_rule = cfg.resolve_path(rule_raw, base=xrc_corner_dir(cfg, cfg.corners[0]))
    for index, ((corner, corner_temperature), published_path) in enumerate(
        zip(cfg.corner_temperature_pairs(), published), start=1
    ):
        safe_corner = _safe_stage_component(corner)
        control = ctx.run_dir / f"_xrc.{index:02d}.{safe_corner}.cal_"
        if shared_rule is None or simple_netlist:
            settings_list.append(
                load_xrc_settings(
                    cfg,
                    ctx,
                    corner_name=corner,
                    control_file=control,
                    output_file=published_path,
                    expected_output_files=(published_path,),
                    rule_file_override=shared_rule,
                    temperature=corner_temperature,
                )
            )
            continue
        base = published_path.with_suffix("")
        native = base.with_name(f"{base.name}_{corner}")
        settings_list.append(
            load_xrc_settings(
                cfg,
                ctx,
                corner_name=corner,
                control_file=control,
                output_file=base,
                expected_output_files=(native,),
                formatter_corners=(corner,),
                rule_file_override=shared_rule,
                temperature=corner_temperature,
            )
        )
    return XrcPlan(tuple(settings_list), False)


def build_xrc_stage_specs(cfg: RceConfig, ctx: DesignContext) -> list[XrcStageSpec]:
    plan = build_xrc_plan(cfg, ctx)
    settings = plan.settings[0]
    calibre = calibre_executable()
    control = settings.control_file.name
    lvs_command = [calibre, "-lvs", "-hier", "-spice", str(settings.layout_netlist)]
    if settings.hcell_file is not None:
        lvs_command.extend(["-hcell", str(settings.hcell_file)])
    lvs_command.extend(["-nowait", control])
    svdb = Path(ctx.svdb_dir)
    stages = [
        XrcStageSpec(
            "xrc_lvs",
            lvs_command,
            ctx.log_dir / "xrc_lvs.log",
            settings.control_file,
            (
                settings.layout_netlist,
                svdb / f"{ctx.layout_cell}.phdb" / "pdb.seg",
                svdb / f"{ctx.layout_cell}.xdb" / "pdb.seg",
            ),
            "lvs",
        ),
    ]
    for item in plan.settings:
        suffix = "" if len(plan.settings) == 1 else f"_{_safe_stage_component(item.corner_name)}"
        item_control = item.control_file.name
        fmt_command = [calibre, "-xrc", "-fmt", *item.fmt_switches]
        if item.formatter_corners:
            fmt_command.extend(["-corner", ",".join(item.formatter_corners)])
        fmt_command.extend(["-nowait", item_control])
        if item.pdb_switch is not None:
            item_pdb = [calibre, "-xrc", "-pdb", item.pdb_switch]
            if item.xcell_file is not None:
                item_pdb.extend(["-xcell", str(item.xcell_file)])
            if item.selection_switch:
                item_pdb.append(item.selection_switch)
            item_pdb.extend(["-turbo", cfg.ext_cpus, "-nowait", item_control])
            stages.append(
                XrcStageSpec(
                    f"xrc_pdb{suffix}",
                    item_pdb,
                    ctx.log_dir / f"xrc_pdb{suffix}.log",
                    item.control_file,
                    (svdb / "pex.db",),
                    "xrc",
                )
            )
        stages.append(
            XrcStageSpec(
                f"xrc_fmt{suffix}",
                fmt_command,
                ctx.log_dir / f"xrc_fmt{suffix}.log",
                item.control_file,
                item.expected_output_files,
                "xrc",
            )
        )
    return stages


def xrc_output_publications(
    cfg: RceConfig, ctx: DesignContext
) -> tuple[tuple[Path, Path], ...]:
    plan = build_xrc_plan(cfg, ctx)
    published = cfg.output_paths(ctx)
    native = tuple(
        path
        for settings in plan.settings
        for path in settings.expected_output_files
    )
    return tuple(zip(native, published))


def _safe_stage_component(value: str) -> str:
    rendered = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    return rendered or "corner"
