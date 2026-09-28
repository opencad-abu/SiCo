"""Render and atomically save one v1 request TOML document."""

from __future__ import annotations

from pathlib import Path
from .model_request import NetlistRequest
from .model_design import TargetSelection
from .model_entries import ModelEntry
from .model_options import ProcessOptions, SimulatorOption
from .toml_values import _toml_string, _toml_scalar, _append_corner_toml


def render_request_toml(request: NetlistRequest) -> str:
    """Render a validated request as a loadable, deterministic TOML file."""

    value = request.validate()
    lines = ['format = "sico-mts-netlistor-request"', "schema_version = 1"]
    _append_corner_toml(lines, value.corner_export, value.temperature_mode)
    lines.extend([
        "",
        "[source]",
        f"cds_lib = {_toml_string(value.source.cds_lib)}",
        f"library = {_toml_string(value.source.library)}",
        f"cell = {_toml_string(value.source.cell)}",
        f"view = {_toml_string(value.source.view)}",
    ])
    if value.source.startup_file is not None:
        lines.append(f"startup_file = {_toml_string(value.source.startup_file)}")
    if value.source.simrc is not None:
        lines.append(f"simrc = {_toml_string(value.source.simrc)}")
    lines.extend(("", "[simulator]", f"dialect = {_toml_string(value.dialect)}"))

    def append_models(models: tuple[ModelEntry, ...], prefix: str = "") -> None:
        header = f"{prefix}.models" if prefix else "models"
        for item in models:
            lines.extend((
                "",
                f"[[{header}]]",
                f"enabled = {_toml_scalar(item.enabled)}",
                f"file = {_toml_string(item.file)}",
                f"section = {_toml_string(item.section)}",
                f"label = {_toml_string(item.label)}",
            ))

    def append_process(process: ProcessOptions, header: str) -> None:
        lines.extend(("", f"[{header}]"))
        for name in ("temp", "tnom", "scale", "scalem", "reltol", "gmin"):
            raw = getattr(process, name)
            if raw is not None:
                lines.append(f"{name} = {_toml_scalar(raw)}")

    def append_options(options: tuple[SimulatorOption, ...], prefix: str = "") -> None:
        header = f"{prefix}.simulator_options" if prefix else "simulator_options"
        for item in options:
            lines.extend((
                "",
                f"[[{header}]]",
                f"enabled = {_toml_scalar(item.enabled)}",
                f"name = {_toml_string(item.name)}",
                f"value_type = {_toml_string(item.value_type)}",
                f"value = {_toml_string(item.value)}",
                "enum_values = [" + ", ".join(_toml_string(item) for item in item.enum_values) + "]",
            ))

    def append_publish(target: TargetSelection, header: str) -> None:
        lines.extend((
            "",
            f"[{header}]",
            f"generate_symbol_view = {_toml_scalar(target.generate_symbol_view)}",
            f"generate_netlist_view = {_toml_scalar(target.generate_netlist_view)}",
            f"target_library = {_toml_string(target.library or '')}",
            f"target_cell = {_toml_string(target.cell or '')}",
            'overwrite = "reject"',
            f"overwrite_symbol_view = {_toml_scalar(target.overwrite_symbol_view)}",
            f"overwrite_netlist_view = {_toml_scalar(target.overwrite_netlist_view)}",
        ))

    if value.cell_specs:
        for spec in value.cell_specs:
            lines.extend((
                "",
                "[[cells]]",
                f"library = {_toml_string(spec.library)}",
                f"cell = {_toml_string(spec.cell)}",
                f"view = {_toml_string(spec.view)}",
                f"dialect = {_toml_string(spec.dialect)}",
            ))
            _append_corner_toml(lines, spec.corner_export, spec.temperature_mode, "cells")
            append_models(spec.models, "cells")
            append_process(spec.process_options, "cells.process_options")
            append_options(spec.simulator_options, "cells")
            append_publish(spec.target or TargetSelection(), "cells.publish")
    else:
        append_models(value.models)
        append_process(value.process_options, "process_options")
        append_options(value.simulator_options)
        append_publish(value.target, "publish")
    return "\n".join(lines).rstrip() + "\n"


def save_request(request: NetlistRequest, path: str | Path) -> Path:
    """Atomically save a GUI/CLI request configuration."""

    from .artifacts import atomic_write_text

    return atomic_write_text(path, render_request_toml(request))
