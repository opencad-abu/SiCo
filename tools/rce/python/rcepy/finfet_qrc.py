"""Resolve Quantus FinFET companions and adapt their design-specific arguments."""

from __future__ import annotations

import shlex
from pathlib import Path

from .config import DesignContext, RceConfig
from .textutil import q, write_text
from .command_paths import command_path


QRC_PROCESS_DEFAULTS = {
    "capacitance": ["-coupling advanced"],
    "extraction_setup": [
        '-net_name_space "LAYOUT"', "-max_via_array_count 4",
        "-background_density_map all 50",
    ],
    "input_db": ["-net_property_value 5", "-instance_property_value 6", "-device_property_value 7"],
}


def optional_qrc_companion(
    cfg: RceConfig, tech_dirs: tuple[Path, ...], field: str, name: str,
) -> Path | None:
    """Return a companion only when every selected corner provides it."""
    configured = cfg.text("extract", "qrc", field).strip()
    paths: list[Path] = []
    for directory in tech_dirs:
        roots = (directory, directory.parent, directory.parent.parent)
        if configured:
            try:
                path = cfg.resolve_path(configured, base=directory)
            except ValueError:
                return None
        else:
            path = next((root / name for root in roots if (root / name).is_file()), None)
        if path is None or not path.is_file():
            return None
        paths.append(path)
    if len({path.read_bytes() for path in paths}) != 1:
        raise ValueError(f"Quantus FinFET corners have incompatible {name} files")
    return paths[0]


def qrc_finfet_technology(
    cfg: RceConfig, ctx: DesignContext, tech_dirs: tuple[Path, ...],
) -> list[str]:
    if not cfg.flag("extract", "finfet_flow"):
        return []
    options: list[str] = []
    command = optional_qrc_companion(
        cfg, tech_dirs, "technology_command_file", "capgen.cmd"
    )
    if command is not None:
        target = ctx.log_dir / "qrc_process.capgen.cmd"
        write_text(target, _capgen_command(command, ctx))
        options.append(f'-technology_command_file "{q(str(target))}"')
    layer = optional_qrc_companion(
        cfg, tech_dirs, "technology_layer_setup_file", "layer_setup"
    )
    if layer is not None:
        options.append(f'-technology_layer_setup_file "{q(str(layer))}"')
    lpe = optional_qrc_companion(
        cfg, tech_dirs, "technology_lpe_confile", "lpe_confile"
    )
    if lpe is not None:
        options.append(f'-technology_lpe_confile "{q(str(lpe))}"')
    return options


def _capgen_command(path: Path, ctx: DesignContext) -> str:
    raw = path.read_text(encoding="utf-8").replace("\\\n", " ")
    tokens = shlex.split(raw, comments=True)
    if not tokens or Path(tokens[0]).name != "Techgen":
        raise ValueError(f"Expected one Techgen command in {path}")
    if "-compilation" not in tokens or tokens[-1] != ".":
        raise ValueError(f"Expected Techgen -compilation ending with '.' in {path}")
    for option, value in (
        ("-lvs_query_directory", command_path(ctx, str(ctx.cci_dir), "qrc")),
        ("-lvs_run_name", ctx.layout_cell),
    ):
        indexes = [i for i, token in enumerate(tokens) if token == option]
        if len(indexes) != 1 or indexes[0] + 1 >= len(tokens) - 1:
            raise ValueError(f"Expected one {option} argument in {path}")
        tokens[indexes[0] + 1] = value
    return shlex.join(tokens) + "\n"
