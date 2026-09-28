"""Read the legacy v1 request document with its existing validation labels."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping
from .toml_backend import tomllib
from .errors import RequestValidationError
from .model_request import NetlistRequest, CellNetlistSpec
from .model_design import SourceDesign, TargetSelection
from .model_options import ProcessOptions
from .config_values import _table, _strict_keys, _required_text, _config_path, _bool
from .config_options import _parse_models, _parse_options, _parse_corner_export
from .config_schema import ROOT_KEYS, SOURCE_KEYS, SIMULATOR_KEYS, PROCESS_KEYS, CELL_KEYS, PUBLISH_KEYS


def load_request(path: str | Path) -> NetlistRequest:
    """Load, resolve, and validate one request TOML document."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise RequestValidationError(f"cannot access request TOML: {source}")
    try:
        with source.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RequestValidationError(f"invalid request TOML: {source}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise RequestValidationError("request TOML root must be a table")
    _strict_keys(raw, ROOT_KEYS, "request root")
    if raw.get("format") != "sico-mts-netlistor-request":
        raise RequestValidationError("request format must be sico-mts-netlistor-request")
    if int(raw.get("schema_version", 0)) != 1:
        raise RequestValidationError("request schema_version must be 1")
    base = source.parent

    source_table = _table(raw.get("source"), "source")
    _strict_keys(source_table, SOURCE_KEYS, "[source]")
    source_design = SourceDesign(
        _config_path(source_table.get("cds_lib"), base, "source.cds_lib"),  # type: ignore[arg-type]
        _required_text(source_table, "library", "source"),
        _required_text(source_table, "cell", "source"),
        str(source_table.get("view", "schematic")).strip() or "schematic",
        _config_path(source_table.get("startup_file"), base, "source.startup_file", required=False),
        _config_path(source_table.get("simrc"), base, "source.simrc", required=False),
    )

    simulator = _table(raw.get("simulator"), "simulator")
    _strict_keys(simulator, SIMULATOR_KEYS, "[simulator]")
    dialect = _required_text(simulator, "dialect", "simulator")

    models = _parse_models(raw.get("models", []), base, "models")

    process_table = _table(raw.get("process_options", {}), "process_options")
    _strict_keys(process_table, PROCESS_KEYS, "[process_options]")
    process = ProcessOptions(**{name: process_table.get(name) for name in PROCESS_KEYS})

    options = _parse_options(raw.get("simulator_options", []), "simulator_options")

    raw_cells = raw.get("cells", [])
    if not isinstance(raw_cells, list):
        raise RequestValidationError("cells must be an array of tables")
    cell_specs: list[CellNetlistSpec] = []
    for index, item in enumerate(raw_cells, start=1):
        table = _table(item, f"cells[{index}]")
        _strict_keys(table, CELL_KEYS, f"cells[{index}]")
        process_table_item = _table(table.get("process_options", {}), f"cells[{index}].process_options")
        _strict_keys(process_table_item, PROCESS_KEYS, f"cells[{index}].process_options")
        cell_publish = _table(table.get("publish", {}), f"cells[{index}].publish")
        _strict_keys(cell_publish, PUBLISH_KEYS, f"cells[{index}].publish")
        cell_specs.append(CellNetlistSpec(
            _required_text(table, "library", f"cells[{index}]"),
            _required_text(table, "cell", f"cells[{index}]"),
            str(table.get("view", "schematic")).strip() or "schematic",
            _parse_models(table.get("models", []), base, f"cells[{index}].models"),
            ProcessOptions(**{name: process_table_item.get(name) for name in PROCESS_KEYS}),
            _parse_options(table.get("simulator_options", []), f"cells[{index}].simulator_options"),
            str(table.get("dialect", dialect)),
            TargetSelection(
                None if str(cell_publish.get("target_library", "")).strip() == "" else str(cell_publish.get("target_library")),
                None if str(cell_publish.get("target_cell", "")).strip() == "" else str(cell_publish.get("target_cell")),
                _bool(cell_publish.get("generate_symbol_view", False), f"cells[{index}].publish.generate_symbol_view"),
                _bool(cell_publish.get("generate_netlist_view", False), f"cells[{index}].publish.generate_netlist_view"),
                str(cell_publish.get("overwrite", "reject")),
                _bool(cell_publish.get("overwrite_symbol_view", False), f"cells[{index}].publish.overwrite_symbol_view"),
                _bool(cell_publish.get("overwrite_netlist_view", False), f"cells[{index}].publish.overwrite_netlist_view"),
            ),
            _parse_corner_export(table.get("corner_export", {}), base, f"cells[{index}].corner_export"),
            str(table.get("temperature_mode", "fixed")),
        ))

    publish_table = _table(raw.get("publish", {}), "publish")
    _strict_keys(publish_table, PUBLISH_KEYS, "[publish]")
    target = TargetSelection(
        None if str(publish_table.get("target_library", "")).strip() == "" else str(publish_table.get("target_library")),
        None if str(publish_table.get("target_cell", "")).strip() == "" else str(publish_table.get("target_cell")),
        _bool(publish_table.get("generate_symbol_view", False), "publish.generate_symbol_view"),
        _bool(publish_table.get("generate_netlist_view", False), "publish.generate_netlist_view"),
        str(publish_table.get("overwrite", "reject")),
        _bool(publish_table.get("overwrite_symbol_view", False), "publish.overwrite_symbol_view"),
        _bool(publish_table.get("overwrite_netlist_view", False), "publish.overwrite_netlist_view"),
    )
    return NetlistRequest(source_design, dialect, tuple(models), process, tuple(options), target, cell_specs=tuple(cell_specs),
        corner_export=_parse_corner_export(raw.get("corner_export", {}), base, "corner_export"),
        temperature_mode=str(raw.get("temperature_mode", "fixed"))).validate()
