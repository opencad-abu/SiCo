"""Resolve Quantus technology files and render process corner options."""

from __future__ import annotations

from pathlib import Path

from .config import DesignContext, RceConfig
from .qrc_defaults import QrcDefaults
from .textutil import clean_lines, q, write_text


def _extract_tech_dir(cfg: RceConfig) -> str:
    return str(extract_tech_dirs(cfg)[0])


def extract_tech_dirs(cfg: RceConfig) -> tuple[Path, ...]:
    explicit = cfg.path("extract", "tech_dir")
    if not explicit:
        raise ValueError(
            "QRC requires extract.tech_dir (Process Directory) to resolve its "
            "technology files"
        )

    process_dir = Path(explicit)
    corners = cfg.corners
    if _is_qrc_tech_dir(process_dir) and len(corners) <= 1:
        return (process_dir,)

    root = process_dir.parent if _is_qrc_tech_dir(process_dir) else process_dir
    available = _qrc_corner_dirs(root)
    if not corners and _is_qrc_tech_dir(process_dir):
        return (process_dir,)

    resolved: list[Path] = []
    for corner in corners:
        exact = root / _qrc_corner_dir(corner)
        if _is_qrc_tech_dir(exact):
            resolved.append(exact)
            continue
        matches = [
            path for path in available if path.name.casefold() == corner.casefold()
        ]
        if len(matches) == 1:
            resolved.append(matches[0])
            continue
        if len(matches) > 1:
            choices = ", ".join(path.name for path in matches)
            raise ValueError(
                f"Ambiguous QRC process corner {corner!r} under Process Directory "
                f"{root}; case-insensitive matches: {choices}. "
                f"Available QRC corners: {_qrc_corner_names(available)}."
            )
        expected = ", ".join(QRC_TECH_FILES)
        raise FileNotFoundError(
            f"Cannot resolve QRC process corner {corner!r} under Process Directory "
            f"{root}; a valid corner must contain one of: {expected}. "
            f"Available QRC corners: {_qrc_corner_names(available)}."
        )
    if resolved:
        return tuple(resolved)

    expected = ", ".join(QRC_TECH_FILES)
    raise FileNotFoundError(
        "Cannot resolve QRC process corner <not set> under Process Directory "
        f"{root}; a valid corner must contain one of: {expected}. "
        f"Available QRC corners: {_qrc_corner_names(available)}."
    )


def qrc_technology_options(
    cfg: RceConfig, ctx: DesignContext, tech_dirs: tuple[Path, ...], baseline: QrcDefaults,
) -> list[str]:
    temperatures = cfg.corner_temperatures()
    temperature_values = list(temperatures)
    if len(set(temperature_values)) == 1:
        temperature_values = temperature_values[:1]
    temperature = " ".join(temperature_values) or cfg.text(
        "extract", "temperature"
    )
    if not cfg.is_multi_corner:
        return [
            f'-technology_directory "{q(str(tech_dirs[0]))}"',
            f"-temperature {temperature}",
        ]

    technology_name = baseline.value("process_technology", "technology_name", "multi_corner")
    if not technology_name:
        raise baseline.source.error(
            1, "Multi-corner extraction requires one -technology_name in [process_technology.multi_corner]"
        )
    wrapper_dir = ctx.log_dir / "qrc_mpc_technology"
    corner_defs = wrapper_dir / "corner.defs"
    techlib_defs = ctx.log_dir / "qrc_mpc_techlib.defs"
    write_text(
        corner_defs,
        clean_lines(
            f"DEFINE {corner} {tech_dir}"
            for corner, tech_dir in zip(cfg.corners, tech_dirs)
        ),
    )
    write_text(techlib_defs, f"DEFINE {technology_name} {wrapper_dir}\n")
    return [
        f'-technology_library_file "{q(str(techlib_defs))}"',
        f"-technology_corner {' '.join(cfg.corners)}",
        f"-temperature {temperature}",
    ]





def _is_qrc_tech_dir(path: Path) -> bool:
    return path.is_dir() and any((path / name).is_file() for name in QRC_TECH_FILES)


def _qrc_corner_dirs(process_dir: Path) -> list[Path]:
    if not process_dir.is_dir():
        return []
    try:
        children = process_dir.iterdir()
        return sorted(
            (path for path in children if _is_qrc_tech_dir(path)),
            key=lambda path: (path.name.casefold(), path.name),
        )
    except OSError as exc:
        raise FileNotFoundError(
            f"Cannot scan QRC Process Directory {process_dir}: {exc}. "
            "Available QRC corners: <unavailable>."
        ) from exc


def _qrc_corner_names(corners: list[Path]) -> str:
    return ", ".join(path.name for path in corners) or "<none>"


def _qrc_corner_dir(corner: str) -> str:
    return str(corner).strip()


QRC_TECH_FILES = ("qrcTechFile", "qrc.tch", "cap_coeff.dat")
