"""Configuration loading and validation for the LEF flow."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .compat import tomllib
from cadconfig.booleans import boolean_value
from cadconfig.paths import resolve_path
from cadconfig.scalars import positive_integer_text, validate_scalars


_BIN_OPTION_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_BIN_OPTION_CHOICES = {
    "PinsTextPreserveLabels": {"true", "false"},
    "PinsRestrictToPRBndry": {"true", "false"},
    "PinsBoundaryCreate": {"off", "always", "as needed"},
    "PinsCreatePwrPinsFromRouting": {"true", "false"},
    "PinsCreatePolyPRB": {"true", "false"},
    "ExtractSig": {"true", "false"},
    "ExtractPwr": {"true", "false"},
    "BlockageCutVia": {"true", "false"},
    "AbstractPinFracture": {"true", "false"},
    "AbstractBlockageFracture": {"true", "false"},
}


def _table(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"TOML section [{name}] must be a table")
    return value


def _text(table: dict[str, Any], key: str, *, default: str | None = None) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"TOML value {key!r} must be a non-empty string")
    return value.strip()


def _optional_text(table: dict[str, Any], key: str) -> str | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"TOML value {key!r} must be a non-empty string")
    return value.strip()


def _text_allow_empty(
    table: dict[str, Any], key: str, *, default: str = ""
) -> str:
    value = table.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"TOML value {key!r} must be a string")
    return value.strip()


def _bin_option_value(name: str, value: Any) -> str:
    if isinstance(value, bool):
        normalized = str(value).lower()
    elif isinstance(value, str):
        normalized = value
    elif isinstance(value, int):
        normalized = str(value)
    elif isinstance(value, float) and math.isfinite(value):
        normalized = str(value)
    else:
        raise ValueError(
            f"abstract.bin_options.{name} must be a string, boolean, or number"
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise ValueError(f"Control characters are not allowed in bin option {name}")
    choices = _BIN_OPTION_CHOICES.get(name)
    if choices is not None and normalized not in choices:
        valid = ", ".join(sorted(choices))
        raise ValueError(f"Invalid value for {name}: {normalized!r}; expected {valid}")
    return normalized


def _bin_options(abstract: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    values = abstract.get("bin_options", {})
    if not isinstance(values, dict):
        raise ValueError("TOML section [abstract.bin_options] must be a table")
    options: list[tuple[str, str]] = []
    for name, value in values.items():
        if not isinstance(name, str) or not _BIN_OPTION_NAME.fullmatch(name):
            raise ValueError(f"Invalid Abstract Generator bin option name: {name!r}")
        options.append((name, _bin_option_value(name, value)))
    return tuple(options)


def _cell_names(values: Any, label: str) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError(f"{label} must contain at least one cell name")
    cells: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} entries must be non-empty strings")
        cell = value.strip()
        if any(ord(char) < 32 or ord(char) == 127 for char in cell):
            raise ValueError(f"Invalid control character in {label}: {cell!r}")
        if cell in seen:
            raise ValueError(f"Duplicate cell in {label}: {cell}")
        cells.append(cell)
        seen.add(cell)
    return tuple(cells)


def _path(
    value: str, base: Path, label: str, *, resolve_symlinks: bool = True,
) -> Path:
    try:
        return resolve_path(value, base, resolve_symlinks=resolve_symlinks)
    except ValueError as exc:
        raise ValueError(f"Invalid path for {label}: {exc}") from exc


@dataclass(frozen=True)
class LefConfig:
    config_path: Path
    run_dir: Path
    cds_lib: Path
    abstract_executable: str
    library: str
    cells: tuple[str, ...]
    source_cell_list: Path | None
    layout_view: str
    logical_view: str
    abstract_view: str
    options_file: Path | None
    bin_name: str
    output_lef: Path
    lef_version: str
    export_geometry: bool
    export_technology: bool
    run_pins: bool
    run_extract: bool
    run_abstract: bool
    bin_options: tuple[tuple[str, str], ...] = ()
    run_type: str = "Current Host"
    queue_name: str = ""
    server_name: str = "localhost"
    cpus: str = "1"

    def validate_inputs(self) -> None:
        if not self.cds_lib.is_file():
            raise FileNotFoundError(f"Cannot access cds.lib: {self.cds_lib}")
        run_generation = self.run_pins or self.run_extract or self.run_abstract
        if self.options_file is None:
            if run_generation:
                raise ValueError("abstract.options_file is required when a step is enabled")
        elif not self.options_file.is_file():
            raise FileNotFoundError(
                f"Cannot access Abstract Generator options: {self.options_file}"
            )
        _cell_names(self.cells, "input cells")
        if not (self.export_geometry or self.export_technology):
            raise ValueError("At least one LEF data type must be enabled")
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", self.lef_version):
            raise ValueError(f"Invalid LEF version: {self.lef_version}")
        if self.run_type not in {"Current Host", "LSF Farm"}:
            raise ValueError(f"Unsupported run.run_type: {self.run_type}")
        if any(ord(char) < 32 or ord(char) == 127 for char in self.queue_name):
            raise ValueError("Control characters are not allowed in run.queue_name")
        if not re.fullmatch(r"[1-9][0-9]*", self.cpus):
            raise ValueError(f"run.cpus must be a positive integer: {self.cpus}")


def _load_cells(inp: dict[str, Any], config_dir: Path) -> tuple[tuple[str, ...], Path | None]:
    cell = _optional_text(inp, "cell")
    cells_value = inp.get("cells")
    cell_list_value = _optional_text(inp, "cell_list_file")
    sources = sum(value is not None for value in (cell, cells_value, cell_list_value))
    if sources != 1:
        raise ValueError(
            "Exactly one of input.cell, input.cells, or input.cell_list_file is required"
        )
    if cell is not None:
        return _cell_names((cell,), "input.cell"), None
    if cells_value is not None:
        return _cell_names(cells_value, "input.cells"), None

    assert cell_list_value is not None
    source = _path(cell_list_value, config_dir, "input.cell_list_file")
    if not source.is_file():
        raise FileNotFoundError(f"Cannot access input cell list: {source}")
    lines = [line.strip() for line in source.read_text(encoding="utf-8").splitlines()]
    return _cell_names([line for line in lines if line], "input.cell_list_file"), source


def load_config(path: str | Path) -> LefConfig:
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Cannot access LEF TOML config: {config_path}")
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    if "cad_config" in data:
        raise ValueError("cad_config identifies a profile; export an execution request first")
    validate_scalars(data)
    run = _table(data, "run")
    inp = _table(data, "input")
    abstract = _table(data, "abstract")
    _table(data, "steps")
    output = _table(data, "output")
    config_dir = config_path.parent
    cells, source_cell_list = _load_cells(inp, config_dir)
    run_pins = boolean_value(data, "steps.pins")
    run_extract = boolean_value(data, "steps.extract")
    run_abstract = boolean_value(data, "steps.abstract")
    options_value = _optional_text(abstract, "options_file")
    run_dir = _path(_text(run, "run_dir"), config_dir, "run.run_dir")
    executable = _text(
        run,
        "executable",
        default=os.environ.get("LEF_ABSTRACT") or "abstract",
    )
    if "/" in executable:
        executable = str(
            _path(
                executable,
                config_dir,
                "run.executable",
                resolve_symlinks=False,
            )
        )

    cfg = LefConfig(
        config_path=config_path,
        run_dir=run_dir,
        cds_lib=_path(_text(run, "cds_lib"), config_dir, "run.cds_lib"),
        abstract_executable=executable,
        library=_text(inp, "library"),
        cells=cells,
        source_cell_list=source_cell_list,
        layout_view=_text(inp, "layout_view", default="layout"),
        logical_view=_text(inp, "logical_view", default="schematic"),
        abstract_view=_text(inp, "abstract_view", default="abstract"),
        options_file=(
            _path(options_value, config_dir, "abstract.options_file")
            if options_value is not None
            else None
        ),
        bin_name=_text(abstract, "bin", default="Core"),
        output_lef=_path(_text(output, "lef_file"), run_dir, "output.lef_file"),
        lef_version=_text(output, "lef_version", default="5.8"),
        export_geometry=boolean_value(data, "output.geometry"),
        export_technology=boolean_value(data, "output.technology"),
        run_pins=run_pins,
        run_extract=run_extract,
        run_abstract=run_abstract,
        bin_options=_bin_options(abstract),
        run_type=_text(run, "run_type", default="Current Host"),
        queue_name=_text_allow_empty(
            run,
            "queue_name",
            default="",
        ),
        server_name=_text_allow_empty(
            run,
            "server_name",
            default=os.environ.get("HOSTNAME") or "localhost",
        ),
        cpus=positive_integer_text(run.get("cpus", "1"), "run.cpus"),
    )
    cfg.validate_inputs()
    return cfg
