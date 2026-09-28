"""Render and atomically save an ordered v2 workspace TOML document."""

from __future__ import annotations

from pathlib import Path
from .workspace_model import WorkspaceConfig
from .model_design import TargetSelection
from .model_entries import ModelEntry
from .model_options import ProcessOptions, SimulatorOption
from .toml_values import _toml_string, _toml_scalar, _append_corner_toml


def render_workspace_toml(workspace: WorkspaceConfig) -> str:
    """Render the complete ordered process workspace as schema v2 TOML."""

    value = workspace.validate()
    lines = [
        'format = "sico-mts-netlistor-workspace"',
        "schema_version = 2",
    ]

    def append_models(models: tuple[ModelEntry, ...], header: str) -> None:
        for item in models:
            lines.extend(
                (
                    "",
                    f"[[{header}]]",
                    f"enabled = {_toml_scalar(item.enabled)}",
                    f"file = {_toml_string(item.file)}",
                    f"section = {_toml_string(item.section)}",
                    f"label = {_toml_string(item.label)}",
                )
            )

    def append_process_options(process: ProcessOptions, header: str) -> None:
        lines.extend(("", f"[{header}]"))
        for field_name in ("temp", "tnom", "scale", "scalem", "reltol", "gmin"):
            raw = getattr(process, field_name)
            if raw is not None:
                lines.append(f"{field_name} = {_toml_scalar(raw)}")

    def append_simulator_options(
        options: tuple[SimulatorOption, ...], header: str
    ) -> None:
        for item in options:
            lines.extend(
                (
                    "",
                    f"[[{header}]]",
                    f"enabled = {_toml_scalar(item.enabled)}",
                    f"name = {_toml_string(item.name)}",
                    f"value_type = {_toml_string(item.value_type)}",
                    f"value = {_toml_string(item.value)}",
                    "enum_values = ["
                    + ", ".join(_toml_string(entry) for entry in item.enum_values)
                    + "]",
                )
            )

    def append_publish(target: TargetSelection, header: str) -> None:
        lines.extend(
            (
                "",
                f"[{header}]",
                f"generate_symbol_view = {_toml_scalar(target.generate_symbol_view)}",
                f"generate_netlist_view = {_toml_scalar(target.generate_netlist_view)}",
                f"target_library = {_toml_string(target.library or '')}",
                f"target_cell = {_toml_string(target.cell or '')}",
                'overwrite = "reject"',
                f"overwrite_symbol_view = {_toml_scalar(target.overwrite_symbol_view)}",
                f"overwrite_netlist_view = {_toml_scalar(target.overwrite_netlist_view)}",
            )
        )

    for process in value.processes:
        request = process.request.validate()
        lines.extend(
            (
                "",
                "[[processes]]",
                f"name = {_toml_string(process.name)}",
                f"project = {_toml_string(process.project)}",
            )
        )
        _append_corner_toml(lines, request.corner_export, request.temperature_mode, "processes")
        lines.extend(
            (
                "",
                "[processes.source]",
                f"cds_lib = {_toml_string(request.source.cds_lib)}",
                f"library = {_toml_string(request.source.library)}",
                f"cell = {_toml_string(request.source.cell)}",
                f"view = {_toml_string(request.source.view)}",
            )
        )
        if request.source.startup_file is not None:
            lines.append(
                f"startup_file = {_toml_string(request.source.startup_file)}"
            )
        if request.source.simrc is not None:
            lines.append(f"simrc = {_toml_string(request.source.simrc)}")
        lines.extend(
            (
                "",
                "[processes.simulator]",
                f"dialect = {_toml_string(request.dialect)}",
            )
        )

        if request.cell_specs:
            for spec in request.cell_specs:
                lines.extend(
                    (
                        "",
                        "[[processes.cells]]",
                        f"library = {_toml_string(spec.library)}",
                        f"cell = {_toml_string(spec.cell)}",
                        f"view = {_toml_string(spec.view)}",
                        f"dialect = {_toml_string(spec.dialect)}",
                    )
                )
                _append_corner_toml(lines, spec.corner_export, spec.temperature_mode, "processes.cells")
                append_models(spec.models, "processes.cells.models")
                append_process_options(
                    spec.process_options, "processes.cells.process_options"
                )
                append_simulator_options(
                    spec.simulator_options,
                    "processes.cells.simulator_options",
                )
                append_publish(
                    spec.target or TargetSelection(), "processes.cells.publish"
                )
        else:
            append_models(request.models, "processes.models")
            append_process_options(
                request.process_options, "processes.process_options"
            )
            append_simulator_options(
                request.simulator_options, "processes.simulator_options"
            )
            append_publish(request.target, "processes.publish")
        for presentation in process.presentation:
            lines.extend(
                (
                    "",
                    "[[processes.presentation]]",
                    f"library = {_toml_string(presentation.library)}",
                    f"cell = {_toml_string(presentation.cell)}",
                    f"view = {_toml_string(presentation.view)}",
                    f"temperature_text = {_toml_string(presentation.temperature_text)}",
                    f"scale_text = {_toml_string(presentation.scale_text)}",
                    f"gmin_text = {_toml_string(presentation.gmin_text)}",
                )
            )
    return "\n".join(lines).rstrip() + "\n"


def save_workspace(workspace: WorkspaceConfig, path: str | Path) -> Path:
    """Atomically save one complete v2 GUI workspace."""

    from .artifacts import atomic_write_text

    return atomic_write_text(path, render_workspace_toml(workspace))
