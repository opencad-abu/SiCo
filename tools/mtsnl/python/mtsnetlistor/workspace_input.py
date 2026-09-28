"""Decode ordered v2 workspace processes and presentation records."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from .toml_backend import tomllib
from .errors import RequestValidationError
from .workspace_model import WorkspaceConfig, WorkspaceProcess, WorkspaceCellPresentation
from .model_request import NetlistRequest, CellNetlistSpec
from .model_design import SourceDesign
from .model_options import ProcessOptions
from .config_values import _table, _strict_keys, _required_text, _config_path
from .config_options import _parse_models, _parse_options, _parse_corner_export, _target_from_mapping
from .config_schema import (WORKSPACE_ROOT_KEYS, WORKSPACE_PROCESS_KEYS, SOURCE_KEYS,
                            SIMULATOR_KEYS, PROCESS_KEYS, CELL_KEYS, PUBLISH_KEYS, PRESENTATION_KEYS)


def _request_from_mapping(
    raw: Mapping[str, Any], base: Path, label: str
) -> NetlistRequest:
    """Parse one request table embedded in a workspace document."""

    _strict_keys(raw, WORKSPACE_PROCESS_KEYS, label)
    source_table = _table(raw.get("source"), f"{label}.source")
    _strict_keys(source_table, SOURCE_KEYS, f"{label}.source")
    source = SourceDesign(
        _config_path(
            source_table.get("cds_lib"), base, f"{label}.source.cds_lib"
        ),  # type: ignore[arg-type]
        _required_text(source_table, "library", f"{label}.source"),
        _required_text(source_table, "cell", f"{label}.source"),
        str(source_table.get("view", "schematic")).strip() or "schematic",
        _config_path(
            source_table.get("startup_file"),
            base,
            f"{label}.source.startup_file",
            required=False,
        ),
        _config_path(
            source_table.get("simrc"),
            base,
            f"{label}.source.simrc",
            required=False,
        ),
    )
    simulator = _table(raw.get("simulator"), f"{label}.simulator")
    _strict_keys(simulator, SIMULATOR_KEYS, f"{label}.simulator")
    dialect = _required_text(simulator, "dialect", f"{label}.simulator")
    models = _parse_models(raw.get("models", []), base, f"{label}.models")
    process_table = _table(
        raw.get("process_options", {}), f"{label}.process_options"
    )
    _strict_keys(process_table, PROCESS_KEYS, f"{label}.process_options")
    process_options = ProcessOptions(
        **{name: process_table.get(name) for name in PROCESS_KEYS}
    )
    options = _parse_options(
        raw.get("simulator_options", []), f"{label}.simulator_options"
    )

    raw_cells = raw.get("cells", [])
    if not isinstance(raw_cells, list):
        raise RequestValidationError(f"{label}.cells must be an array of tables")
    specs: list[CellNetlistSpec] = []
    for cell_index, item in enumerate(raw_cells, start=1):
        cell_label = f"{label}.cells[{cell_index}]"
        table = _table(item, cell_label)
        _strict_keys(table, CELL_KEYS, cell_label)
        cell_process = _table(
            table.get("process_options", {}), f"{cell_label}.process_options"
        )
        _strict_keys(
            cell_process, PROCESS_KEYS, f"{cell_label}.process_options"
        )
        cell_publish = _table(
            table.get("publish", {}), f"{cell_label}.publish"
        )
        _strict_keys(cell_publish, PUBLISH_KEYS, f"{cell_label}.publish")
        specs.append(
            CellNetlistSpec(
                _required_text(table, "library", cell_label),
                _required_text(table, "cell", cell_label),
                str(table.get("view", "schematic")).strip() or "schematic",
                _parse_models(
                    table.get("models", []), base, f"{cell_label}.models"
                ),
                ProcessOptions(
                    **{name: cell_process.get(name) for name in PROCESS_KEYS}
                ),
                _parse_options(
                    table.get("simulator_options", []),
                    f"{cell_label}.simulator_options",
                ),
                str(table.get("dialect", dialect)),
                _target_from_mapping(cell_publish, f"{cell_label}.publish"),
                _parse_corner_export(table.get("corner_export", {}), base, f"{cell_label}.corner_export"),
                str(table.get("temperature_mode", "fixed")),
            )
        )
    publish = _table(raw.get("publish", {}), f"{label}.publish")
    _strict_keys(publish, PUBLISH_KEYS, f"{label}.publish")
    return NetlistRequest(
        source,
        dialect,
        tuple(models),
        process_options,
        tuple(options),
        _target_from_mapping(publish, f"{label}.publish"),
        cell_specs=tuple(specs),
        corner_export=_parse_corner_export(raw.get("corner_export", {}), base, f"{label}.corner_export"),
        temperature_mode=str(raw.get("temperature_mode", "fixed")),
    ).validate()


def _presentation_from_mapping(
    raw: Any, label: str
) -> tuple[WorkspaceCellPresentation, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise RequestValidationError(f"{label} must be an array of tables")
    values: list[WorkspaceCellPresentation] = []
    for index, item in enumerate(raw, start=1):
        item_label = f"{label}[{index}]"
        table = _table(item, item_label)
        _strict_keys(table, PRESENTATION_KEYS, item_label)
        values.append(
            WorkspaceCellPresentation(
                _required_text(table, "library", item_label),
                _required_text(table, "cell", item_label),
                str(table.get("view", "schematic")).strip() or "schematic",
                str(table.get("temperature_text", "")),
                str(table.get("scale_text", "")),
                str(table.get("gmin_text", "")),
            ).validate()
        )
    return tuple(values)


def load_workspace(path: str | Path) -> WorkspaceConfig:
    """Load and validate one v2 multi-process workspace TOML document."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise RequestValidationError(f"cannot access workspace TOML: {source}")
    try:
        with source.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RequestValidationError(
            f"invalid workspace TOML: {source}: {exc}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise RequestValidationError("workspace TOML root must be a table")
    _strict_keys(raw, WORKSPACE_ROOT_KEYS, "workspace root")
    if raw.get("format") != "sico-mts-netlistor-workspace":
        raise RequestValidationError(
            "workspace format must be sico-mts-netlistor-workspace"
        )
    schema_version = raw.get("schema_version")
    # Keep malformed TOML types inside the public validation contract.  A
    # direct ``int(...)`` here would leak ValueError/TypeError through the GUI
    # instead of producing the same actionable RequestValidationError used by
    # all other workspace fields.
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 2:
        raise RequestValidationError("workspace schema_version must be 2")
    raw_processes = raw.get("processes")
    if not isinstance(raw_processes, list) or not raw_processes:
        raise RequestValidationError(
            "workspace processes must be a non-empty array of tables"
        )
    processes: list[WorkspaceProcess] = []
    for index, item in enumerate(raw_processes, start=1):
        label = f"processes[{index}]"
        table = _table(item, label)
        request = _request_from_mapping(table, source.parent, label)
        processes.append(
            WorkspaceProcess(
                str(table.get("name", f"Process{index}")),
                request,
                _presentation_from_mapping(
                    table.get("presentation"), f"{label}.presentation"
                ),
                str(table.get("project", "")),
            )
        )
    return WorkspaceConfig(tuple(processes)).validate()
