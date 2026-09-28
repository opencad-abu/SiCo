"""Shared OpenAccess parasitic-view configuration and file resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import DesignContext, RceConfig
from .textutil import write_text


@dataclass(frozen=True)
class OaViewTarget:
    library: str
    cell: str
    view: str
    kind: str


def tool_key(cfg: RceConfig) -> str:
    tool = cfg.ext_tool.strip().casefold().replace("-", "").replace("_", "")
    if tool in {"qrc", "quantus"}:
        return "qrc"
    if tool in {"calxrc", "xrc", "calibrexrc"}:
        return "xrc"
    if tool in {"starrc", "starxtract"}:
        return "starrc"
    raise ValueError(f"Unsupported extraction tool for OA view output: {cfg.ext_tool}")


def view_kind(cfg: RceConfig) -> str:
    output_type = cfg.output_type.strip().casefold()
    tool = tool_key(cfg)
    legacy = {
        "extview": ("qrc", "extracted"),
        "smartview": ("qrc", "smart"),
        "calibreview": ("xrc", "calibre"),
        "starrcview": ("starrc", "oa"),
    }
    if output_type in legacy:
        required_tool, kind = legacy[output_type]
        if tool != required_tool:
            raise ValueError(
                f"RCE output type {cfg.output_type!r} is only supported by "
                f"{required_tool}; use output_type='view' for {cfg.ext_tool}"
            )
        return kind
    if output_type != "view":
        raise ValueError(f"RCE output type {cfg.output_type!r} is not an OA view output")

    raw_kind = cfg.text("extract", "view", "kind").strip().casefold()
    aliases = {
        "qrc": {
            "": "smart",
            "smart": "smart",
            "smartview": "smart",
            "smart_view": "smart",
            "extracted": "extracted",
            "extview": "extracted",
            "extracted_view": "extracted",
        },
        "xrc": {"": "calibre", "calibre": "calibre", "calibreview": "calibre"},
        "starrc": {
            "": "oa",
            "oa": "oa",
            "starrc": "oa",
            "parasitic": "oa",
            "oa_view": "oa",
        },
    }
    try:
        return aliases[tool][raw_kind]
    except KeyError as exc:
        supported = ", ".join(sorted(key for key in aliases[tool] if key))
        raise ValueError(
            f"Unsupported extract.view.kind {raw_kind!r} for {cfg.ext_tool}; "
            f"use one of: {supported}"
        ) from exc


def resolve_view_target(cfg: RceConfig, ctx: DesignContext) -> OaViewTarget:
    tool = tool_key(cfg)
    kind = view_kind(cfg)
    library = cfg.text("extract", "view", "library").strip()
    cell = cfg.text("extract", "view", "cell").strip()
    if bool(library) != bool(cell):
        raise ValueError(
            "extract.view.library and extract.view.cell must be specified together"
        )

    if not library:
        library, cell = _implicit_view_target(cfg, ctx, tool)

    if not library or not cell:
        raise ValueError(
            f"{cfg.ext_tool} OA view output has no OA target; provide an OA-backed "
            "design input or set extract.view.library and extract.view.cell"
        )
    default_name = {
        "smart": "av_extracted",
        "extracted": "av_extracted",
        "calibre": "calibre",
        "oa": "starrc",
    }[kind]
    view = cfg.text("extract", "view", "name", default=default_name).strip()
    _validate_oa_name(library, "extract.view.library")
    _validate_oa_name(cell, "extract.view.cell")
    _validate_oa_name(view, "extract.view.name")
    return OaViewTarget(library, cell, view, kind)


def qrc_layout_view(cfg: RceConfig, ctx: DesignContext) -> str:
    value = cfg.text("extract", "view", "layout_view").strip()
    if not value and ctx.input_type in {"OA", "CDL+LAY"}:
        value = cfg.text("input", "layout", "view").strip()
    if not value and ctx.input_type == "SCH+GDS":
        # Quantus still requires a layout-view token in -design_cell_name.  The
        # top view itself may be absent; in that case it creates pins from the
        # extracted-netlist labels.
        value = "layout"
    if not value and ctx.input_type in {"CDL+GDS", "SVDB", "CCI"}:
        # These inputs do not have an implicit OA target, but an explicitly
        # supplied View Target still needs a layout-view token in Quantus'
        # -design_cell_name triplet.  Match the conventional OA layout view
        # used by the GUI when no project-specific token was supplied.
        value = "layout"
    if not value:
        raise ValueError(
            "Quantus view output requires an OA layout view; set input.layout.view "
            "or extract.view.layout_view"
        )
    _validate_oa_name(value, "extract.view.layout_view")
    return value


def _implicit_view_target(
    cfg: RceConfig, ctx: DesignContext, tool: str
) -> tuple[str, str]:
    if tool == "qrc":
        if ctx.input_type == "SCH+GDS":
            return (
                cfg.text("input", "schematic", "lib").strip(),
                ctx.source_cell,
            )
        if ctx.input_type in {"OA", "CDL+LAY"}:
            return (
                cfg.text("input", "layout", "lib").strip(),
                ctx.layout_cell,
            )
        return "", ""

    if ctx.name_source == "schematic" and ctx.input_type in {"OA", "SCH+GDS"}:
        return (
            cfg.text("input", "schematic", "lib").strip(),
            ctx.source_cell,
        )
    if ctx.name_source == "layout" and ctx.input_type in {"OA", "CDL+LAY"}:
        return (
            cfg.text("input", "layout", "lib").strip(),
            ctx.layout_cell,
        )
    return "", ""


def oa_library_definitions(cfg: RceConfig) -> Path:
    raw = cfg.text("run", "cds_lib").strip()
    if not raw:
        raise ValueError("OA view output requires run.cds_lib")
    path = cfg.resolve_path(raw)
    if not path.is_file():
        raise FileNotFoundError(f"Cannot access OA library definitions file: {path}")
    return path


def resolved_corner_dir(cfg: RceConfig) -> Path:
    raw = cfg.text("extract", "tech_dir").strip()
    if not raw:
        raise ValueError("OA view mapping discovery requires extract.tech_dir")
    tech_dir = cfg.resolve_path(raw)
    corner = cfg.corners[0] if cfg.corners else ""
    if not corner or tech_dir.name.casefold() == corner.casefold():
        return tech_dir
    return tech_dir / corner


def calibre_cellmap_file(cfg: RceConfig) -> Path:
    raw = cfg.text("extract", "view", "cellmap_file").strip() or cfg.text(
        "extract", "xrc", "cellmap_file"
    ).strip()
    path = cfg.resolve_path(raw) if raw else resolved_corner_dir(cfg) / "calview.cellmap"
    if not path.is_file():
        raise FileNotFoundError(f"Cannot access Calibre View cellmap file: {path}")
    _validate_setup_path(path, "Calibre View cellmap file")
    return path


def starrc_mapping_files(cfg: RceConfig) -> tuple[Path, Path]:
    corner_dir = resolved_corner_dir(cfg)
    device = _mapping_file(
        cfg,
        "device_mapping_file",
        corner_dir,
        (
            "OA_DEVICE_MAP",
            "oa_device_map",
            "oa_device_mapping_file",
            "device_mapping_file",
            "DFII_DEVICE_MAP",
        ),
        "StarRC OA device mapping file",
    )
    layer = _mapping_file(
        cfg,
        "layer_mapping_file",
        corner_dir,
        (
            "OA_LAYER_MAP",
            "oa_layer_map",
            "oa_layer_mapping_file",
            "layer_mapping_file",
            "DFII_LAYER_MAP",
        ),
        "StarRC OA layer mapping file",
    )
    return device, layer


def write_calibreview_setup(
    cfg: RceConfig, ctx: DesignContext, output_file: Path
) -> Path:
    target = resolve_view_target(cfg, ctx)
    if target.kind != "calibre":
        raise ValueError("CalibreView setup requested for a non-Calibre view output")
    schematic_library = cfg.text(
        "extract", "view", "schematic_library"
    ).strip() or cfg.text("input", "schematic", "lib").strip()
    if not schematic_library:
        schematic_library = target.library
    _validate_oa_name(schematic_library, "extract.view.schematic_library")
    _validate_setup_path(output_file, "CalibreView netlist")
    cellmap = calibre_cellmap_file(cfg)
    setup = ctx.log_dir / "calibreview.setup"
    log_file = ctx.log_dir / "calview.log"
    lines = [
        f"calibre_view_netlist_file : {output_file}",
        f"output_library : {target.library}",
        f"schematic_library : {schematic_library}",
        f"cell_name : {target.cell}",
        f"cellmap_file : {cellmap}",
        f"calibreview_log_file : {log_file}",
        f"calibreview_name : {target.view}",
        "calibreview_type : maskLayout",
        "create_terminals : if_matching",
        "preserve_device_case : off",
        "execute_callbacks : off",
        "suppress_notes : off",
        "reset_properties : (m=1)",
        "magnify_devices_by : 1",
        "magnify_parasitics_by : 1",
        "device_placement : layout_location",
        "parasitic_placement : arrayed",
        "show_parasitic_polygons : off",
        "open_calibreview : don't_open",
        "generate_spectre_netlist : off",
    ]
    return write_text(setup, "\n".join(lines) + "\n")


def _mapping_file(
    cfg: RceConfig,
    option: str,
    corner_dir: Path,
    candidates: tuple[str, ...],
    label: str,
) -> Path:
    raw = cfg.text("extract", "view", option).strip() or cfg.text(
        "extract", "starrc", option
    ).strip()
    if raw:
        path = cfg.resolve_path(raw)
    else:
        path = next(
            (corner_dir / name for name in candidates if (corner_dir / name).is_file()),
            corner_dir / candidates[0],
        )
    if not path.is_file():
        raise FileNotFoundError(
            f"Cannot access {label}: {path}. Set extract.view.{option} to the "
            "foundry-provided StarRC OA mapping file; stream-out layer maps are "
            "not compatible."
        )
    return path


def _validate_oa_name(value: str, option: str) -> None:
    if not value or any(char.isspace() for char in value) or "/" in value:
        raise ValueError(f"Invalid OA name for {option}: {value!r}")


def _validate_setup_path(path: Path, label: str) -> None:
    value = str(path)
    if any(char in value for char in ("\n", "\r", ":")) or " " in value:
        raise ValueError(
            f"{label} cannot contain spaces, newlines, or ':' in CalibreView setup: "
            f"{path}"
        )
