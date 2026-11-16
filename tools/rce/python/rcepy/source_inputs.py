"""Prepare optional CDL device definitions for generated and external sources."""

from __future__ import annotations

from pathlib import Path

from .config import DesignContext, RceConfig
from .process_inputs import existing_input_file, optional_input_file
from .textutil import q, write_text


def source_added_file(cfg: RceConfig) -> str:
    explicit = optional_input_file(cfg, "input", "source_added_file")
    if explicit or not cfg.flag("extract", "finfet_flow"):
        return explicit
    roots = []
    tech = cfg.path("extract", "tech_dir")
    if tech:
        root = Path(tech)
        roots.extend([root / corner for corner in cfg.corners])
        roots.extend((root, root.parent))
    deck = cfg.path("lvs", "runset_file")
    if deck:
        roots.append(Path(deck).parent)
    reference = cfg.path("extract", "starrc", "reference_file")
    if reference:
        roots.append(Path(reference).parent)
    candidates = [root / "source.added" for root in roots if (root / "source.added").is_file()]
    if len({path.read_bytes() for path in candidates}) > 1:
        raise ValueError("Ambiguous FinFET source.added files; set RCE_CDL_SOURCE_ADDED_FILE")
    return str(candidates[0].resolve()) if candidates else ""


def cdl_header_file(cfg: RceConfig, ctx: DesignContext) -> str:
    header = existing_input_file(cfg, "input", "schematic", "cdl_header_file")
    added = source_added_file(cfg)
    if not added:
        return header
    paths = list(dict.fromkeys(path for path in (header, added) if path))
    return str(write_text(
        ctx.cdl_dir / "process.includes.cdl",
        "".join(f'.INCLUDE "{q(path)}"\n' for path in paths),
    ))


def lvs_source_path(cfg: RceConfig, ctx: DesignContext) -> str:
    added = source_added_file(cfg)
    if not added or "cdl" in cfg.enabled_stages():
        return ctx.source_path
    return str(write_text(
        ctx.log_dir / "source.with_devices.cdl",
        f'.INCLUDE "{q(ctx.source_path)}"\n.INCLUDE "{q(added)}"\n',
    ))
