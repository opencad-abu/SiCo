"""Map Quantus output destinations and multi-corner publication names."""

from __future__ import annotations

from pathlib import Path

from .config import DesignContext, RceConfig

# Quantus transistor-level flows default to one multi-value netlist; these
# formats must switch to ``-output_multi_value_rc false`` for the per-corner
# publication below to have single-value files to map.
QRC_PER_CORNER_OUTPUT_TYPES = frozenset({"dspf", "spef"})


def qrc_per_corner_output(cfg: RceConfig, output_type: str) -> bool:
    """Return whether Quantus writes one single-value file per selected corner."""
    return (
        cfg.is_multi_corner
        and output_type.strip().casefold() in QRC_PER_CORNER_OUTPUT_TYPES
    )


def qrc_output_target(
    cfg: RceConfig, ctx: DesignContext, output_type: str
) -> str:
    output = Path(cfg.output_path(ctx))
    if qrc_per_corner_output(cfg, output_type):
        return str(output.with_suffix(""))
    return str(output)



def qrc_output_publications(
    cfg: RceConfig, ctx: DesignContext
) -> tuple[tuple[Path, Path], ...]:
    """Map native Quantus MPC names to stable RCE output names."""
    published = cfg.output_paths(ctx)
    if not cfg.is_multi_corner or len(published) <= 1:
        return tuple((path, path) for path in published)
    output_type = cfg.output_type.strip().casefold()
    if not qrc_per_corner_output(cfg, output_type):
        return tuple((path, path) for path in published)
    base = Path(cfg.output_path(ctx)).with_suffix("")
    temperatures = cfg.corner_temperatures()
    include_temperature = len(set(temperatures)) > 1
    native = tuple(
        base.with_name(
            f"{base.name}_{corner}"
            f"{f'_{temperature}' if include_temperature else ''}.{output_type}"
        )
        for corner, temperature in cfg.corner_temperature_pairs()
    )
    return tuple(zip(native, published))
