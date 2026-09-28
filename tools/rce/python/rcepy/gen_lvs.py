"""Calibre LVS command generation."""

from __future__ import annotations

from pathlib import Path

from caddefaults import Defaults
from cadcalibre.defaults import calibre_defaults

from cadcalibre.lvs import (
    append_custom_svrf as append_calibre_custom_svrf,
    hcell_arguments,
    virtual_connect_lines,
)

from .config import DesignContext, RceConfig
from .textutil import clean_lines, q, write_text


def resolve_lvs_hcell_file(
    cfg: RceConfig,
    *,
    default_enabled: bool = False,
    fallback_value: str = "",
    fallback_base: Path | None = None,
    fallback_file: Path | None = None,
    label: str = "Calibre LVS hcell file",
) -> Path | None:
    """Resolve the optional command-line hcell list for a Calibre LVS stage."""
    if not cfg.flag("lvs", "hcell_enable", default=default_enabled):
        return None

    raw = cfg.text("lvs", "hcell_file").strip()
    if raw:
        path = cfg.resolve_path(raw)
    elif fallback_value.strip():
        path = cfg.resolve_path(fallback_value, base=fallback_base)
    elif fallback_file is not None:
        path = fallback_file.resolve()
    else:
        raise ValueError(
            "Calibre LVS hcell is enabled but lvs.hcell_file is empty"
        )

    if not path.is_file():
        raise FileNotFoundError(f"Cannot access {label}: {path}")
    return path


def lvs_hcell_arguments(cfg: RceConfig) -> list[str]:
    hcell_file = resolve_lvs_hcell_file(cfg)
    return hcell_arguments(hcell_file)


def lvs_virtual_connect_lines(cfg: RceConfig) -> list[str]:
    """Render the shared Calibre LVS/xRC virtual-connect controls."""
    settings = cfg.section("lvs")
    if "virtual_connect_enable" in settings or "virtual_connect_name_enable" in settings:
        colon = "t" if cfg.flag("lvs", "virtual_connect_enable") else "nil"
        name = "t" if cfg.flag("lvs", "virtual_connect_name_enable") else "nil"
        mode = f"({colon} {name})"
    else:
        mode = str(cfg.get("lvs", "virtual_connect", default=""))
    return virtual_connect_lines(
        mode,
        cfg.items("lvs", "virtual_connect_names"),
    )


def lvs_recognize_gates_line(cfg: RceConfig) -> str:
    mode = cfg.text("lvs", "recognize_gates", default="NONE").strip().upper()
    if mode not in {"NONE", "ALL", "SIMPLE"}:
        raise ValueError(
            f"Unsupported lvs.recognize_gates {mode!r}; expected NONE, ALL or SIMPLE"
        )
    return f"LVS RECOGNIZE GATES {mode}"


def append_custom_svrf(text: str, cfg: RceConfig) -> str:
    """Append enabled user SVRF verbatim to a generated control file."""
    return append_calibre_custom_svrf(
        text,
        enabled=cfg.flag("lvs", "custom_svrf_enable", default=False),
        command=cfg.text("lvs", "custom_svrf_command"),
    )


def lvs_svdb_query_tokens(cfg: RceConfig) -> list[str]:
    """Return the selected Calibre SVDB query tokens.

    An explicitly empty ``lvs.svdb_query`` means the user selected no optional
    query data and must render as a bare ``QUERY`` clause.  Configurations
    without this standalone-LVS option retain the standalone legacy default.
    """
    raw = cfg.get("lvs", "svdb_query", default=None)
    if raw is None:
        return ["CCI", "PINLOC"]

    values = cfg.items("lvs", "svdb_query")
    tokens: list[str] = []
    allowed = {"CCI", "SI", "RECON", "IXF", "NXF", "SLPH", "PINLOC"}
    for value in values:
        parts = value.replace("+", " ").split()
        normalized = [part.upper() for part in parts]
        unknown = [part for part in normalized if part not in allowed]
        if unknown:
            raise ValueError(
                "Unsupported lvs.svdb_query token(s): " + ", ".join(unknown)
            )
        tokens.extend(normalized)
    return tokens


def generate_lvs(
    cfg: RceConfig, ctx: DesignContext, *, standalone: bool = False, defaults: Defaults | None = None
) -> Path:
    # RCE needs connectivity data only. Extra query outputs belong to the
    # standalone LVS flow and must not leak into extraction runsets.
    query_tokens = lvs_svdb_query_tokens(cfg) if standalone else ["CCI"]
    baseline = calibre_defaults("calibre-lvs.svrf", defaults, ctx.log_dir)
    runset = cfg.path("lvs", "runset_file")
    lines = [
        f'LAYOUT PATH "{q(ctx.layout_path)}"',
        f"LAYOUT PRIMARY {ctx.layout_cell}",
        "LAYOUT SYSTEM GDSII",
        f'SOURCE PATH "{q(ctx.source_path)}"',
        f"SOURCE PRIMARY {ctx.source_cell}",
        "SOURCE SYSTEM SPICE",
        f'MASK SVDB DIRECTORY "{q(ctx.svdb_dir)}" QUERY'
        + (" " + " ".join(query_tokens) if query_tokens else ""),
        f'LVS REPORT "{q(str(ctx.log_dir / "lvs.report"))}"',
        lvs_recognize_gates_line(cfg),
    ]
    if cfg.lvs_case_sensitive():
        lines.extend(["SOURCE CASE YES", "LAYOUT CASE YES"])

    lines.extend(lvs_virtual_connect_lines(cfg))

    cells = cfg.blocked_cells()
    if cells:
        hcells = ctx.log_dir / "hcells"
        write_text(hcells, "".join(f"HCELL {cell} {cell}\n" for cell in cells))
        lines.append(f'INCLUDE "{q(str(hcells))}"')

    text = append_custom_svrf(clean_lines(lines), cfg).rstrip("\n")
    text += "\n\n"
    text += "\n".join(
        (
            *baseline.get("before_erc_results", []),
            f'ERC RESULTS DATABASE "{q(str(ctx.log_dir / "erc.results"))}"',
            *baseline.get("after_erc_results", []),
        )
    )
    if runset:
        text += f'\nINCLUDE "{q(runset)}"'
    text += "\n"
    return write_text(ctx.log_dir / "lvs.cal", text)
