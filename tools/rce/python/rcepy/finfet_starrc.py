"""Import only technology-owned commands from a foundry StarRC reference."""

from __future__ import annotations

from pathlib import Path

from .config import DesignContext, RceConfig
from .starrc_common import reject_cumulative_common_commands
from .starrc_inputs import _starrc_tech_dirs, starrc_common_options
from .textutil import q


_TECHNOLOGY = frozenset("""
MULTIGATE_MODELS DPT IGNORE_GATE_CHANNEL_CAPACITANCE REMOVE_DIFFUSION_GATE_OVERLAP
TRANSLATE_FLOATING_AS_FILL IGNORE_CAPACITANCE TRANSLATE_RETAIN_BULK_LAYERS
DENSITY_BASED_THICKNESS DENSITY_OUTSIDE_BLOCK EXTRACT_VIA_CAPS
TRENCH_CONTACT_VIRTUAL_VIA_SEGMENTATION_RATIO MAX_VIRTUAL_VIA_SEGMENTATION_NUMBER
COUPLE_TO_PCELL_PINS COUPLE_TO_SPECIFIED_PCELL_PINS EXTRACT_RES_BODY_COUPLING
CALIBRE_LVS_DEVICE_TYPE_RES CALIBRE_LVS_DEVICE_TYPE_MOS CALIBRE_OPTIONAL_DEVICE_PIN_FILE
SKIP_PCELLS LPE_PARAM LPE_DEVICES METAL_FILL_POLYGON_HANDLING REMOVE_DANGLING_NETS
""".split())


def _optional_reference(cfg: RceConfig, corner: str | None) -> Path | None:
    raw = cfg.text("extract", "starrc", "reference_file").strip()
    if raw:
        try:
            path = cfg.resolve_path(raw)
        except ValueError:
            return None
        return path if path.is_file() else None
    root, corner_dir = _starrc_tech_dirs(cfg, corner)
    path = next(
        (candidate / "star_cmd" for candidate in (corner_dir, root)
         if (candidate / "star_cmd").is_file()),
        None,
    )
    return path


def starrc_references(cfg: RceConfig) -> tuple[Path, ...]:
    paths = []
    for corner in cfg.corners or (None,):
        path = _optional_reference(cfg, corner)
        if path is None:
            return ()
        paths.append(path)
    return tuple(paths)


def _technology_lines(path: Path) -> tuple[list[str], list[Path]]:
    records = []
    files = []
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition(":")
        key, value = key.strip().upper(), value.strip()
        if not sep or key not in _TECHNOLOGY:
            continue
        if not value:
            raise ValueError(f"Empty StarRC technology command {key} in {path}")
        if key == "CALIBRE_OPTIONAL_DEVICE_PIN_FILE":
            pin = path.parent / value.strip('"')
            if not pin.is_file():
                raise FileNotFoundError(f"Cannot access StarRC device pin file: {pin}")
            value = f'"{q(str(pin.resolve()))}"'
            files.append(pin.resolve())
        records.append(f"{key}: {value}")
    if "MULTIGATE_MODELS: YES" not in records:
        raise ValueError(f"FinFET StarRC reference must enable MULTIGATE_MODELS: {path}")
    return records, files


def starrc_process_files(cfg: RceConfig) -> tuple[Path, ...]:
    references = starrc_references(cfg)
    if not references:
        return ()
    return (*references, *(file for ref in references for file in _technology_lines(ref)[1]))


def apply_starrc_process(cfg: RceConfig, ctx: DesignContext, lines: list[str]) -> list[str]:
    if not cfg.flag("extract", "finfet_flow"):
        return lines
    references = starrc_references(cfg)
    if not references:
        return lines
    selected, signatures = [], []
    for path in references:
        records, files = _technology_lines(path)
        signature = ([line for line in records if not line.startswith("CALIBRE_OPTIONAL_DEVICE_PIN_FILE:")],
                     [file.read_bytes() for file in files])
        if signatures and signature != signatures[0]:
            raise ValueError("StarRC FinFET corners require identical shared technology settings")
        signatures.append(signature)
        if not selected:
            selected = records
    for corner in cfg.corners or (None,):
        common, common_path = starrc_common_options(cfg, corner)
        reject_cumulative_common_commands(
            common, _TECHNOLOGY, include_base=ctx.run_dir, common_opt=common_path,
            purpose="FinFET StarRC technology settings",
        )
    keys = {line.partition(":")[0] for line in selected}
    return [line for line in lines if line.partition(":")[0].strip() not in keys] + [
        "", "*** Foundry FinFET technology settings", *selected,
    ]
