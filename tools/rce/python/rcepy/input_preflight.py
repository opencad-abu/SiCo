"""RCE input path enumeration and managed-location validation."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .config import RceConfig
from .config_context import DesignContext
from .reduction import reduction_enabled
from .xrc import build_xrc_plan


def configured_input_paths(
    cfg: RceConfig,
    context: DesignContext,
) -> list[tuple[str, Path]]:
    """Return configured file or directory inputs that affect an RCE run."""

    paths: list[tuple[str, Path]] = []

    def add(*keys: str, name_source: bool = False) -> None:
        value = cfg.get(*keys)
        if not isinstance(value, str) or not value.strip():
            return
        try:
            path = cfg.resolve_path(value)
        except ValueError:
            if name_source:
                return
            raise
        if not name_source or path.is_file():
            paths.append((".".join(keys), path))

    add("run", "cds_lib")
    add("extract", "tech_dir")
    stages = cfg.enabled_stages()
    if "cdl" in stages:
        add("input", "schematic", "cdl_header_file")
    if "gds" in stages:
        add("input", "layout", "layer_map")
    if cfg.input_type in {"CDL+GDS", "CDL+LAY"}:
        add("input", "cdl", "file")
    if cfg.input_type in {"CDL+GDS", "SCH+GDS"}:
        add("input", "gds", "file")
    if cfg.input_type in {"CCI", "SVDB"}:
        add("input", cfg.input_type.lower(), "dir")
    if "lvs" in stages or cfg.ext_tool.upper() in {"STARRC", "STARXTRACT"}:
        add("lvs", "runset_file")
        if cfg.flag("lvs", "hcell_enable"):
            add("lvs", "hcell_file")
    if cfg.is_xrc:
        for settings in build_xrc_plan(cfg, context).settings:
            for name in (
                "lvs_rule_file",
                "rule_file",
                "base_hcell_file",
                "base_xcell_file",
            ):
                path = getattr(settings, name)
                if path is not None:
                    paths.append((f"extract.xrc.{name}", path.resolve()))
    if cfg.flag("netlist", "pin_order_enable") and cfg.text(
        "netlist", "pin_order_type"
    ).strip().casefold() in {"file", "user defined file"}:
        add("netlist", "pin_order_file")
    for kind, key in (("net", "nets"), ("cell", "cells")):
        if cfg.flag("selection", f"{kind}_enable"):
            add("selection", key, name_source=True)
    if cfg.is_view_output:
        if cfg.ext_tool.upper() in {"QRC", "QUANTUS"} and "cdl" not in stages:
            add("input", "cdl", "run_directory")
        for key in ("cellmap_file", "device_mapping_file", "layer_mapping_file"):
            add("extract", "view", key)
        if cfg.is_xrc:
            add("extract", "xrc", "cellmap_file")
    if cfg.ext_tool.upper() in {"STARRC", "STARXTRACT"}:
        for key in ("tcad_grd_file", "mapping_file"):
            add("extract", "starrc", key)
        if cfg.flag("extract", "starrc", "three_d_ic"):
            add("extract", "starrc", "three_d_ic_subckt_file")
    if reduction_enabled(cfg):
        add("reduction", "canonical_device_file")
        if cfg.text("reduction", "mode").strip().casefold() in {
            "selection",
            "selection file",
        }:
            add("reduction", "selection_file")
    return paths


def validate_input_locations(
    cfg: RceConfig,
    context: DesignContext,
    output_paths: Iterable[Path],
    reduced_paths: Iterable[Path] = (),
) -> None:
    """Reject configured inputs that overlap managed directories or outputs."""

    outputs = {path.resolve() for path in (*output_paths, *reduced_paths)}
    managed = (context.db_dir.resolve(), context.log_dir.resolve())
    for name, path in configured_input_paths(cfg, context):
        if path in outputs or any(
            path == root or root in path.parents for root in managed
        ):
            raise ValueError(
                f"Input {name} overlaps RCE-managed data: {path}; "
                "move the input outside db/log and output paths before running"
            )


__all__ = ["configured_input_paths", "validate_input_locations"]
