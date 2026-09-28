"""Resolve and validate StarRC technology and common option inputs."""

from __future__ import annotations

from pathlib import Path

from .config import RceConfig
from .starrc_common import (
    load_common_options,
)


def require_starrc_file(
    path: Path,
    label: str,
    *,
    nonempty: bool = False,
) -> Path:
    """Require a regular, readable StarRC input file."""
    try:
        if not path.is_file():
            raise FileNotFoundError
        if nonempty and path.stat().st_size == 0:
            raise ValueError("empty")
        with path.open("rb") as stream:
            if nonempty and not stream.read(1):
                raise ValueError("empty")
    except (OSError, ValueError) as exc:
        raise FileNotFoundError(f"Cannot access StarRC {label}: {path}") from exc
    return path


def _starrc_tech_dirs(
    cfg: RceConfig, corner_name: str | None = None
) -> tuple[Path, Path]:
    tech_raw = cfg.text("extract", "tech_dir").strip()
    if not tech_raw:
        raise ValueError(
            "StarRC requires extract.tech_dir when an input file override is empty"
        )
    tech_dir = cfg.resolve_path(tech_raw)
    corner = (
        corner_name
        if corner_name is not None
        else (cfg.corners[0] if cfg.corners else "")
    ).strip()
    if corner and tech_dir.name.casefold() != corner.casefold():
        return tech_dir, tech_dir / corner
    if corner:
        return tech_dir.parent, tech_dir
    return tech_dir, tech_dir


def _find_starrc_file(
    corner_dir: Path,
    *,
    label: str,
    option: str,
    preferred_names: tuple[str, ...],
    fallback_pattern: str,
    secondary_candidates: tuple[Path, ...] = (),
) -> Path:
    for name in preferred_names:
        candidate = corner_dir / name
        if candidate.is_file():
            return candidate

    matches = sorted(path for path in corner_dir.glob(fallback_pattern) if path.is_file())
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        choices = ", ".join(str(path) for path in matches)
        raise ValueError(
            f"Ambiguous StarRC {label} files in {corner_dir}: {choices}. "
            f"Set {option} explicitly."
        )
    for candidate in secondary_candidates:
        if candidate.is_file():
            return candidate

    expected = ", ".join(preferred_names)
    secondary = ""
    if secondary_candidates:
        secondary = " Shared mapping candidates: " + ", ".join(
            str(path) for path in secondary_candidates
        ) + "."
    raise FileNotFoundError(
        f"Cannot find a StarRC {label} in {corner_dir}; expected {expected} "
        f"or one {fallback_pattern} file.{secondary} Set {option} explicitly "
        "if the PDK uses a different layout."
    )


def resolve_starrc_inputs(
    cfg: RceConfig, corner_name: str | None = None
) -> tuple[Path, Path]:
    tcad_raw = cfg.text("extract", "starrc", "tcad_grd_file").strip()
    mapping_raw = cfg.text("extract", "starrc", "mapping_file").strip()
    tcad_grd_file = (
        require_starrc_file(cfg.resolve_path(tcad_raw), "TCAD grid file")
        if tcad_raw
        else None
    )
    mapping_file = (
        require_starrc_file(cfg.resolve_path(mapping_raw), "mapping file")
        if mapping_raw
        else None
    )
    if tcad_grd_file is not None and mapping_file is not None:
        if corner_name is not None:
            raise ValueError(
                "StarRC multi-corner extraction cannot use one explicit "
                "tcad_grd_file/mapping_file pair; put those files in each "
                "corner technology directory"
            )
        return tcad_grd_file, mapping_file

    tech_root, corner_dir = _starrc_tech_dirs(cfg, corner_name)
    if not corner_dir.is_dir():
        raise FileNotFoundError(f"Cannot access StarRC corner directory: {corner_dir}")
    if tcad_grd_file is None:
        tcad_grd_file = _find_starrc_file(
            corner_dir,
            label="TCAD grid",
            option="extract.starrc.tcad_grd_file",
            preferred_names=("nxtgrd",),
            fallback_pattern="*.nxtgrd",
        )
    if mapping_file is None:
        mapping_file = _find_starrc_file(
            corner_dir,
            label="mapping",
            option="extract.starrc.mapping_file",
            preferred_names=("tran.map", "map", "MAPPING_FILE", "mapping_file"),
            fallback_pattern="*.map",
            secondary_candidates=(
                tech_root / "MAPPING_FILE",
                tech_root / "mapping_file",
            ) if tech_root != corner_dir else (),
        )
    return tcad_grd_file, mapping_file


def starrc_common_options(
    cfg: RceConfig, corner_name: str | None = None
) -> tuple[list[str], Path | None]:
    tech_raw = cfg.text("extract", "tech_dir").strip()
    if not tech_raw:
        return [], None
    _, corner_dir = _starrc_tech_dirs(cfg, corner_name)
    return load_common_options(corner_dir / "common.opt")
