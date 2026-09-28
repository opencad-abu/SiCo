"""Map Quantus output destinations and multi-corner publication names."""

from __future__ import annotations

from pathlib import Path

from .config import DesignContext, RceConfig


def qrc_output_target(
    cfg: RceConfig, ctx: DesignContext, output_type: str
) -> str:
    output = Path(cfg.output_path(ctx))
    if cfg.is_multi_corner and output_type in {"dspf", "spef"}:
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
    if output_type not in {"dspf", "spef"}:
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
