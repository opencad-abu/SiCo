"""Generate deterministic Abstract Generator replay inputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import LefConfig


STANDARD_BINS = ("Core", "Block", "IO", "Corner", "Ignore")


@dataclass(frozen=True)
class ReplayArtifacts:
    replay_file: Path
    cell_list_file: Path
    abstract_log: Path
    lefout_log: Path


def skill_string(value: str) -> str:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("SKILL replay values cannot contain control characters")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _option(name: str, value: str) -> str:
    return f"absSetOption({skill_string(name)} {skill_string(value)})"


def _bin_option(bin_name: str, name: str, value: str) -> str:
    return (
        f"absSetBinOption({skill_string(bin_name)} "
        f"{skill_string(name)} {skill_string(value)})"
    )


def generate_replay(cfg: LefConfig) -> ReplayArtifacts:
    log_dir = cfg.run_dir / "log"
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    cfg.output_lef.parent.mkdir(parents=True, exist_ok=True)

    replay_file = cfg.run_dir / "lef.replay.il"
    cell_list_file = cfg.run_dir / "lef.cells.list"
    abstract_log = log_dir / "abstract.log"
    lefout_log = cfg.run_dir / "lefout.log"
    cell_list_file.write_text("\n".join(cfg.cells) + "\n", encoding="utf-8")

    lines = [
        "absSkillMode()",
        _option("ViewLayout", cfg.layout_view),
        _option("ViewLogical", cfg.logical_view),
        _option("ViewAbstract", cfg.abstract_view),
        f"absSetLibrary({skill_string(cfg.library)})",
    ]
    run_generation = cfg.run_pins or cfg.run_extract or cfg.run_abstract
    if run_generation:
        assert cfg.options_file is not None
        lines.extend(
            (
                _option("ImportOptionsFile", str(cfg.options_file)),
                "absImportOptions()",
            )
        )
    lines.extend(
        (
            _option("ViewLayout", cfg.layout_view),
            _option("ViewLogical", cfg.logical_view),
            _option("ViewAbstract", cfg.abstract_view),
            _option("DefaultBin", cfg.bin_name),
        )
    )
    if run_generation:
        lines.extend(
            _bin_option(cfg.bin_name, name, value)
            for name, value in cfg.bin_options
        )
    for cell in cfg.cells:
        lines.extend(
            (
                "absDeselectCells()",
                f"absSelectCellFrom({skill_string(cell)} {skill_string(cell)})",
                f"absMoveSelectedCellsToBin({skill_string(cfg.bin_name)})",
            )
        )
    lines.append("absDisableUpdate()")
    lines.extend(
        f"absDeselectBinFrom({skill_string(name)} {skill_string(name)})"
        for name in STANDARD_BINS
        if name != cfg.bin_name
    )
    lines.extend(
        [
            f"absSelectBinFrom({skill_string(cfg.bin_name)} {skill_string(cfg.bin_name)})",
            "absDeselectCells()",
        ]
    )
    lines.extend(f"absSelectCell({skill_string(cell)})" for cell in cfg.cells)
    lines.extend(
        (
            "absEnableUpdate()",
            _option("ExportLEFFile", str(cfg.output_lef)),
            _option("ExportLEFCellListFile", str(cell_list_file)),
            _option("ExportGeometryLefData", str(cfg.export_geometry).lower()),
            _option("ExportTechLefData", str(cfg.export_technology).lower()),
            _option("ExportLEFVersion", cfg.lef_version),
        )
    )
    for enabled, command in (
        (cfg.run_pins, "absPins()"),
        (cfg.run_extract, "absExtract()"),
        (cfg.run_abstract, "absAbstract()"),
    ):
        if enabled:
            lines.append(command)
    lines.extend(("absExportLEF()", "absExit()"))
    replay_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ReplayArtifacts(replay_file, cell_list_file, abstract_log, lefout_log)
