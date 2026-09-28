"""TOML configuration loading and normalization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from .compat import tomllib
from .pathutil import read_name_source, resolve_path
from cadconfig.booleans import boolean_value
from cadconfig.values import MISSING, freeze, thaw
from .config_input import execution_values
from .config_context import DesignContext


INPUTS_NEED_CDL = {"OA", "SCH+GDS"}
INPUTS_NEED_GDS = {"OA", "CDL+LAY"}
INPUTS_NEED_LVS = {"OA", "SCH+GDS", "CDL+LAY", "CDL+GDS"}
XRC_TOOLS = {"CALXRC", "XRC", "CALIBRE-XRC", "CALIBRE_XRC"}
PAIRED_INPUT_TYPES = {"OA", "SCH+GDS", "CDL+LAY", "CDL+GDS"}
VIEW_OUTPUT_TYPES = {"view", "extview", "smartview", "calibreview", "starrcview"}
def load_config(path: str | Path, *, legacy: bool = False) -> "RceConfig":
    cfg_path = Path(path).expanduser().resolve()
    with cfg_path.open("rb") as fh:
        raw = tomllib.load(fh)
    if legacy:
        from .config_legacy import adapt_legacy

        raw = adapt_legacy(raw)
    return RceConfig(raw=raw, config_path=cfg_path)
def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if text.startswith("(") and text.endswith(")"):
        return [item for item in text[1:-1].replace('"', "").split() if item]
    return [item for item in text.replace(",", " ").split() if item]
@dataclass(frozen=True)
class RceConfig:
    raw: Mapping[str, Any]
    config_path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", freeze(execution_values(self.raw)))
        object.__setattr__(self, "config_path", Path(self.config_path).expanduser().resolve())
    def to_dict(self) -> dict[str, Any]:
        """Return a detached projection suitable for constructing a new request."""
        return thaw(self.raw)

    def replace(self, *path: str, value: Any) -> "RceConfig":
        """Return a validated snapshot with one field replaced."""
        if not path:
            raise ValueError("A configuration replacement needs a field path")
        raw = self.to_dict()
        node = raw
        for key in path[:-1]:
            node = node.setdefault(key, {})
            if not isinstance(node, dict):
                raise ValueError(f"Configuration replacement crosses non-table field {key}")
        node[path[-1]] = thaw(value)
        return RceConfig(raw, self.config_path)

    def section(self, *names: str) -> Mapping[str, Any]:
        node: Any = self.raw
        for name in names:
            if not isinstance(node, Mapping):
                return freeze({})
            node = node.get(name, freeze({}))
        return node if isinstance(node, Mapping) else freeze({})

    def get(self, *path: str, default: Any = "") -> Any:
        node: Any = self.raw
        for name in path:
            if not isinstance(node, Mapping) or name not in node:
                node = MISSING
                break
            node = node[name]
        if node is not MISSING:
            return node
        return default if default is MISSING else freeze(default)

    def text(self, *path: str, default: str = "") -> str:
        value = self.get(*path, default=default)
        return "" if value is None else str(value)

    def flag(self, *path: str, default: Any = MISSING) -> bool:
        return boolean_value(self.raw, ".".join(path), default)

    def lvs_case_sensitive(self) -> bool:
        """Use strict source/layout name matching unless explicitly disabled."""
        return self.flag("lvs", "case_sensitive")

    def items(self, *path: str) -> list[str]:
        return _as_list(self.get(*path, default=[]))

    def source_items(self, *path: str) -> list[str]:
        return read_name_source(
            self.get(*path, default=[]), self.config_path.parent
        )

    def net_selection(self) -> tuple[str | None, list[str]]:
        """Return the normalized Include/Exclude mode and selected net names."""
        if not self.flag("selection", "net_enable"):
            return None, []

        raw_mode = self.text(
            "selection", "net_type", default="Include Nets"
        )
        modes = {
            "include nets": "include",
            "exclude nets": "exclude",
        }
        try:
            mode = modes[raw_mode.strip().casefold()]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported net selection type: {raw_mode!r}; "
                "use 'Include Nets' or 'Exclude Nets'"
            ) from exc

        names = self.source_items("selection", "nets")
        if not names:
            raise ValueError(
                "Net selection is enabled but no net names or net-list file was provided"
            )
        return mode, names

    def blocked_cells(self) -> list[str]:
        """Return user block-cell names after rejecting the removed hierarchy mode."""
        if not self.flag("selection", "cell_enable"):
            return []

        raw_type = self.text(
            "selection", "cell_type", default="Block Cells"
        )
        if raw_type.strip().casefold() != "block cells":
            raise ValueError(
                f"Unsupported cell selection type: {raw_type!r}; "
                "only 'Block Cells' is supported"
            )

        names = self.source_items("selection", "cells")
        if not names:
            raise ValueError(
                "Block Cells is enabled but no cell names or cell-list file was provided"
            )
        return names

    def parasitic_info_flags(self) -> tuple[bool, bool, bool]:
        return tuple(self.flag("netlist", name) for name in (
            "parasitic_coordinates",
            "parasitic_res_layer",
            "parasitic_res_dimensions",
        ))

    def resolve_path(self, value: Any, *, base: str | Path | None = None) -> Path:
        root = self.config_path.parent if base is None else Path(base)
        return resolve_path(value, root)

    def path(
        self, *keys: str, default: str = "",
        base: str | Path | None = None,
    ) -> str:
        value = self.get(*keys, default=default)
        if value is None or not str(value).strip():
            return ""
        try:
            return str(self.resolve_path(value, base=base))
        except ValueError as exc:
            name = ".".join(keys) or "path"
            raise ValueError(f"Invalid path for {name}: {exc}") from exc

    @property
    def run_dir(self) -> Path:
        value = self.text("run", "run_dir")
        if not value:
            raise ValueError("Missing run.run_dir in flow TOML")
        return self.resolve_path(value)

    @property
    def input_type(self) -> str:
        return self.text("input", "type").upper()

    @property
    def lvs_tool(self) -> str:
        return self.text("lvs", "tool", default="Calibre")

    @property
    def ext_tool(self) -> str:
        return self.text("extract", "tool", default="QRC")

    @property
    def corners(self) -> tuple[str, ...]:
        """Return selected process corners in stable, de-duplicated order.

        ``extract.corner`` is the original single-corner spelling.  New TOML
        files may use ``extract.corners``; accepting both here keeps all
        generators and the command-line flow independent of the UI format.
        """
        configured = self.get("extract", "corners", default=None)
        if configured is None:
            configured = self.get("extract", "corner", default=None)
        if configured is None:
            return ()
        values: list[str] = []
        seen: set[str] = set()
        for item in _as_list(configured):
            name = str(item).strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            values.append(name)
        return tuple(values)

    @property
    def is_multi_corner(self) -> bool:
        return len(self.corners) > 1

    @property
    def is_multi_corner_scope(self) -> bool:
        """Return whether the profile explicitly selects Multiple Corners mode."""
        return self.text("extract", "corner_scope", default="").strip().casefold() == (
            "multiple corners"
        )

    def corner_temperatures(self) -> tuple[str, ...]:
        """Return one temperature per selected corner when configured.

        The GUI writes an aligned ``corner_temperatures`` array.  A TOML table
        keyed by corner name is also accepted for hand-authored files; the
        scalar ``extract.temperature`` supplies the default for missing values.
        """
        corners = self.corners
        default = self.text("extract", "temperature")
        raw = self.get("extract", "corner_temperatures", default=None)
        if raw is None:
            return tuple(default for _ in corners)
        if isinstance(raw, Mapping):
            normalized = {
                str(key).strip().casefold(): str(value).strip()
                for key, value in raw.items()
            }
            return tuple(normalized.get(corner.casefold(), default) for corner in corners)
        values = _as_list(raw)
        if len(values) == 1 and len(corners) > 1:
            values *= len(corners)
        if values and len(values) != len(corners):
            raise ValueError(
                "extract.corner_temperatures must contain one value per selected "
                f"corner ({len(corners)} expected, got {len(values)})"
            )
        return tuple(values or (default for _ in corners))

    def corner_temperature_pairs(self) -> tuple[tuple[str, str], ...]:
        return tuple(zip(self.corners, self.corner_temperatures()))

    @property
    def is_xrc(self) -> bool:
        return self.ext_tool.upper() in XRC_TOOLS

    @property
    def start_rve(self) -> bool:
        return self.is_xrc and self.flag("extract", "start_rve", default=False)

    @property
    def output_type(self) -> str:
        return self.text("extract", "output_type", default="dspf")

    @property
    def is_view_output(self) -> bool:
        return self.output_type.strip().casefold() in VIEW_OUTPUT_TYPES

    @property
    def top_cell_source(self) -> str:
        value = self.text(
            "extract",
            "top_cell_source",
            default="layout",
        )
        return self._normalize_name_source(value, "extract.top_cell_source")

    @staticmethod
    def _normalize_name_source(value: Any, option: str) -> str:
        normalized = str(value).strip().casefold()
        aliases = {
            "schematic": "schematic",
            "source": "schematic",
            "sch": "schematic",
            "layout": "layout",
            "lay": "layout",
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported {option}: {normalized!r}; "
                "use 'schematic' or 'layout'"
            ) from exc

    @property
    def name_source_configured(self) -> bool:
        return self.get(
            "extract", "name_source", default=None
        ) is not None

    def _name_source_for_context(self, top_cell_source: str) -> str:
        value = self.get(
            "extract", "name_source", default=None
        )
        if value is not None:
            return self._normalize_name_source(value, "extract.name_source")

        # Before name_source existed, StarRC naming was controlled directly by
        # extract.starrc.xref. Keep that behavior for existing TOML files.
        if self.ext_tool.strip().casefold() == "starrc":
            xref = self.get("extract", "starrc", "xref", default="YES")
            if isinstance(xref, bool):
                return "schematic" if xref else "layout"
            normalized = str(xref).strip().upper()
            return (
                "layout"
                if normalized in {"NO", "FALSE", "F", "0"}
                else "schematic"
            )
        return top_cell_source

    @property
    def lvs_cpus(self) -> str:
        return self.text("runtime", "lvs_cpus", default="1")

    @property
    def ext_cpus(self) -> str:
        return self.text("runtime", "ext_cpus", default="1")

    def calibre_run_mode(self, flow: str) -> str:
        """Return the validated Calibre application mode for DRC or LVS."""
        if flow not in {"drc", "lvs"}:
            raise ValueError(f"Unsupported Calibre flow for run mode: {flow!r}")
        raw = self.text(flow, "run_mode", default="Hier").strip().casefold()
        modes = {
            "hier": "Hier",
            "hierarchical": "Hier",
            "flat": "Flat",
        }
        try:
            return modes[raw]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported {flow}.run_mode: {raw!r}; use 'Hier' or 'Flat'"
            ) from exc

    def output_path(self, ctx: DesignContext) -> str:
        configured = self.text("netlist", "output_path")
        if configured:
            return self.path("netlist", "output_path", base=ctx.run_dir)
        output_type = self.output_type.strip().casefold()
        if output_type in VIEW_OUTPUT_TYPES:
            if self.is_xrc:
                return str(ctx.db_dir / f"{ctx.source_cell}.pex.netlist")
            return ""
        suffix = {
            "sp": "sp",
            "spice": "sp",
            "hspice": "sp",
            "dspf": "dspf",
            "spf": "spf",
            "spef": "spef",
            "spectre": "scs",
        }.get(output_type, output_type or "dspf")
        return str(ctx.db_dir / f"{ctx.top_cell}.{suffix}")

    def output_paths(self, ctx: DesignContext) -> tuple[Path, ...]:
        """Return the stable RCE-published output for each selected corner."""
        output = self.output_path(ctx)
        if not output or self.is_view_output:
            return ()
        if not self.is_multi_corner:
            return (Path(output),)
        # Quantus SPICE output is one vector netlist for all corners.  Other
        # file formats are published as ``<stem>_<corner><suffix>``.
        if self.ext_tool.upper() in {"QRC", "QUANTUS"} and self.output_type.strip().casefold() in {
            "sp", "spice", "hspice"
        }:
            return (Path(output),)
        base = Path(output)
        return tuple(
            base.with_name(f"{base.stem}_{corner}{base.suffix}")
            for corner in self.corners
        )

    def context(self) -> DesignContext:
        run_dir = self.run_dir
        db_dir = run_dir / "db"
        input_type = self.input_type

        sch_cell = self.text("input", "schematic", "cell")
        lay_cell = self.text("input", "layout", "cell")
        cdl_cell = self.text("input", "cdl", "cell")
        gds_cell = self.text("input", "gds", "cell")
        svdb_cell = self.text("input", "svdb", "cell")
        cci_cell = self.text("input", "cci", "cell")

        if input_type in {"OA", "SCH+GDS"}:
            source_cell = sch_cell
            source_path = str(db_dir / "cdl" / f"{sch_cell}.cdl")
        elif input_type in {"CDL+LAY", "CDL+GDS"}:
            source_cell = cdl_cell
            source_path = self.path("input", "cdl", "file")
        elif input_type == "SVDB":
            source_cell = svdb_cell
            source_path = ""
        elif input_type == "CCI":
            source_cell = cci_cell
            source_path = ""
        else:
            raise ValueError(f"Unsupported input type: {input_type}")

        if input_type in {"OA", "CDL+LAY"}:
            layout_cell = lay_cell
            layout_path = str(db_dir / "gds" / f"{lay_cell}.gds")
        elif input_type in {"SCH+GDS", "CDL+GDS"}:
            layout_cell = gds_cell
            layout_path = self.path("input", "gds", "file")
        elif input_type == "SVDB":
            layout_cell = svdb_cell
            layout_path = ""
        else:
            layout_cell = cci_cell
            layout_path = ""

        if input_type in PAIRED_INPUT_TYPES:
            top_cell_source = self.top_cell_source
            top_cell = (
                source_cell if top_cell_source == "schematic" else layout_cell
            )
            svdb_dir = str(db_dir / f"svdb.{layout_cell}")
            cci_dir = str(db_dir / f"cci.{layout_cell}")
        elif input_type == "SVDB":
            top_cell_source = "layout"
            top_cell = layout_cell
            svdb_dir = self.path("input", "svdb", "dir")
            cci_dir = str(db_dir / f"cci.{layout_cell}")
        else:
            top_cell_source = "layout"
            top_cell = layout_cell
            svdb_dir = ""
            cci_dir = self.path("input", "cci", "dir")

        name_source = self._name_source_for_context(top_cell_source)

        pin_source = source_path
        if self.text("netlist", "pin_order_type") == "User Defined File":
            pin_source = self.path(
                "netlist", "pin_order_file"
            )

        return DesignContext(
            input_type=input_type,
            run_dir=run_dir,
            log_dir=run_dir / "log",
            db_dir=db_dir,
            cdl_dir=db_dir / "cdl",
            gds_dir=db_dir / "gds",
            top_cell_source=top_cell_source,
            name_source=name_source,
            top_cell=top_cell,
            source_cell=source_cell,
            source_path=source_path,
            layout_cell=layout_cell,
            layout_path=layout_path,
            svdb_dir=svdb_dir,
            cci_dir=cci_dir,
            pin_order_source=pin_source,
        )

    def enabled_stages(self) -> list[str]:
        stages: list[str] = []
        if self.input_type in INPUTS_NEED_CDL:
            stages.append("cdl")
        if self.input_type in INPUTS_NEED_GDS:
            stages.append("gds")
        if self.input_type in INPUTS_NEED_LVS and not self.is_xrc:
            stages.append("lvs")
        if self.input_type != "CCI" and not self.is_xrc:
            stages.append("query")
        stages.append("extract")
        return stages
