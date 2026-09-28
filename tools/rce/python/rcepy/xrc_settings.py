"""Validate and resolve the settings for one Calibre XRC extraction."""

from __future__ import annotations

from pathlib import Path

from .config import DesignContext, RceConfig
from .gen_lvs import resolve_lvs_hcell_file
from .oa_view import view_kind
from .xrc_cells import validate_xrc_block_cells
from .xrc_models import XrcSettings
from .xrc_options import (
    bracket_replacement,
    pin_order_statement,
    selection_switch,
    xrc_mode_switches,
)
from .xrc_paths import require_xrc_file, xrc_corner_dir


def load_xrc_settings(
    cfg: RceConfig,
    ctx: DesignContext,
    *,
    corner_name: str | None = None,
    control_file: Path | None = None,
    output_file: Path | None = None,
    expected_output_files: tuple[Path, ...] | None = None,
    formatter_corners: tuple[str, ...] = (),
    rule_file_override: Path | None = None,
    temperature: str | None = None,
) -> XrcSettings:
    if ctx.input_type not in XRC_INPUT_TYPES:
        raise ValueError(
            "Calibre XRC requires layout and source input; supported input types are "
            "OA, SCH+GDS, CDL+LAY, and CDL+GDS"
        )

    config_base = cfg.config_path.parent
    tech_raw = cfg.text("extract", "tech_dir")
    tech_dir = cfg.resolve_path(tech_raw) if tech_raw else config_base
    corner = (
        corner_name
        if corner_name is not None
        else (cfg.corners[0] if cfg.corners else "")
    ).strip()
    corner_dir = xrc_corner_dir(cfg, corner) if corner else tech_dir
    rule_raw = cfg.text("extract", "xrc", "rule_file") or cfg.text(
        "extract", "xrc", "deck_file"
    )
    lvs_rule_raw = cfg.text(
        "lvs", "runset_file"
    ).strip()
    if not lvs_rule_raw:
        raise ValueError("Calibre XRC requires lvs.runset_file")
    lvs_rule_file = cfg.resolve_path(lvs_rule_raw)
    rule_file = (
        rule_file_override
        if rule_file_override is not None
        else cfg.resolve_path(rule_raw, base=corner_dir)
        if rule_raw
        else corner_dir / "xrc.cal"
    )
    legacy_hcell_raw = cfg.text("extract", "xrc", "hcell_file")
    hcell_file = resolve_lvs_hcell_file(
        cfg,
        default_enabled=True,
        fallback_value=legacy_hcell_raw,
        fallback_base=corner_dir,
        fallback_file=corner_dir / "hcell_list",
        label="Calibre XRC hcell file",
    )
    require_xrc_file(lvs_rule_file, "Calibre XRC LVS rule file")
    require_xrc_file(rule_file, "Calibre XRC rule file")
    if lvs_rule_file.resolve() == rule_file.resolve():
        raise ValueError(
            "Calibre XRC requires different files for lvs.runset_file and "
            "extract.xrc.rule_file"
        )

    blocked_cells = tuple(cfg.blocked_cells())
    explicit_xcell = cfg.text("extract", "xrc", "xcell_file").strip()
    base_xcell_file: Path | None = None
    if explicit_xcell:
        base_xcell_file = cfg.resolve_path(explicit_xcell, base=corner_dir)
        require_xrc_file(base_xcell_file, "Calibre XRC xcell file")
    else:
        candidate = corner_dir / "xcell_list"
        if candidate.is_file():
            base_xcell_file = candidate.resolve()

    base_hcell_file = hcell_file
    if (blocked_cells or base_xcell_file is not None) and base_hcell_file is None:
        raise ValueError(
            "Calibre XRC xcell extraction requires LVS hcell to be enabled"
        )
    if blocked_cells:
        validate_xrc_block_cells(
            blocked_cells,
            base_xcell_file=base_xcell_file,
            base_hcell_file=base_hcell_file,
        )
        xcell_file = ctx.log_dir / "xcell_list.merged"
        hcell_file = ctx.log_dir / "hcell_list.merged"
    else:
        xcell_file = base_xcell_file

    pdb_switch, fmt_switches = xrc_mode_switches(cfg)
    simple_netlist = pdb_switch is None
    output_type = cfg.output_type.lower()
    if cfg.is_view_output and view_kind(cfg) != "calibre":
        raise ValueError("Calibre XRC view output requires a Calibre View kind")
    try:
        netlist_format = XRC_NETLIST_FORMATS[output_type]
    except KeyError as exc:
        mode = " NONE/noRC" if simple_netlist else ""
        raise ValueError(
            f"Calibre XRC{mode} output supports only the RCE formats: "
            "view, dspf, and spice"
        ) from exc

    output_path = cfg.output_path(ctx)
    if not output_path:
        raise ValueError("Calibre XRC requires a file-based netlist output")
    selected_output = output_file or Path(output_path)
    selected_expected = expected_output_files or (selected_output,)
    character_map_line, bus_delimiter = bracket_replacement(cfg)
    return XrcSettings(
        control_file=control_file or ctx.run_dir / "_xrc.cal_",
        lvs_rule_file=lvs_rule_file,
        rule_file=rule_file,
        hcell_file=hcell_file,
        xcell_file=xcell_file,
        base_hcell_file=base_hcell_file,
        base_xcell_file=base_xcell_file,
        blocked_cells=blocked_cells,
        layout_netlist=Path(ctx.svdb_dir) / f"{ctx.layout_cell}.sp",
        lvs_report=ctx.log_dir / "xrc_lvs.report",
        output_file=selected_output,
        expected_output_files=selected_expected,
        formatter_corners=formatter_corners,
        corner_name=corner,
        temperature=(
            temperature
            if temperature is not None
            else cfg.text("extract", "temperature")
        ),
        netlist_format=netlist_format,
        pdb_switch=pdb_switch,
        fmt_switches=fmt_switches,
        simple_netlist=simple_netlist,
        selection_switch=selection_switch(cfg, pdb_switch),
        pin_order_line=pin_order_statement(cfg),
        character_map_line=character_map_line,
        bus_delimiter=bus_delimiter,
    )


XRC_INPUT_TYPES = {"OA", "SCH+GDS", "CDL+LAY", "CDL+GDS"}


XRC_NETLIST_FORMATS = {
    "dspf": "DSPF",
    "sp": "HSPICE",
    "spice": "HSPICE",
    "view": "CALIBREVIEW",
    "calibreview": "CALIBREVIEW",
}
