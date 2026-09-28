"""Immutable ordered GUI workspace values and presentation text."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from .errors import RequestValidationError
from .model_request import NetlistRequest
from .model_validation import validate_oa_name


@dataclass(frozen=True)
class WorkspaceCellPresentation:
    """Raw process-field text kept separately from typed request semantics."""

    library: str
    cell: str
    view: str = "schematic"
    temperature_text: str = ""
    scale_text: str = ""
    gmin_text: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.library, self.cell, self.view)

    def validate(self) -> "WorkspaceCellPresentation":
        library = validate_oa_name(self.library, "presentation library")
        cell = validate_oa_name(self.cell, "presentation cell")
        view = validate_oa_name(self.view or "schematic", "presentation view")
        texts = (
            self.temperature_text,
            self.scale_text,
            self.gmin_text,
        )
        for name, text in zip(("temperature_text", "scale_text", "gmin_text"), texts):
            if not isinstance(text, str):
                raise RequestValidationError(f"presentation {name} must be a string")
            if any(ord(char) < 32 or char in "\r\n" for char in text):
                raise RequestValidationError(f"presentation {name} contains a control character")
        return WorkspaceCellPresentation(library, cell, view, *texts)


@dataclass(frozen=True)
class WorkspaceProcess:
    """One named process tab in a workspace configuration."""

    name: str
    request: NetlistRequest
    presentation: tuple[WorkspaceCellPresentation, ...] = ()
    project: str = ""

    def validate(self, index: int = 1) -> "WorkspaceProcess":
        name = str(self.name).strip() or f"Process{index}"
        if any(ord(char) < 32 or char in "\r\n" for char in name):
            raise RequestValidationError(
                f"processes[{index}].name cannot contain control characters"
            )
        project = str(self.project).strip()
        if any(ord(char) < 32 or char in "\r\n" for char in project):
            raise RequestValidationError(
                f"processes[{index}].project cannot contain control characters"
            )
        if project:
            project_path = Path(project)
            if (
                project_path.is_absolute()
                or ".." in project_path.parts
                or any(char in project for char in ("\\", os.pathsep))
            ):
                raise RequestValidationError(
                    f"processes[{index}].project must be a module name"
                )
        presentation = tuple(item.validate() for item in self.presentation)
        request = self.request.validate()
        request_keys = {
            (spec.library, spec.cell, spec.view) for spec in request.selected_cells
        }
        keys: set[tuple[str, str, str]] = set()
        for item in presentation:
            if item.key not in request_keys:
                raise RequestValidationError(
                    f"workspace presentation cell is not selected: {'/'.join(item.key)}"
                )
            if item.key in keys:
                raise RequestValidationError(
                    f"duplicate workspace presentation cell: {'/'.join(item.key)}"
                )
            keys.add(item.key)
        return WorkspaceProcess(name, request, presentation, project)


@dataclass(frozen=True)
class WorkspaceConfig:
    """The complete ordered multi-process GUI workspace."""

    processes: tuple[WorkspaceProcess, ...]
    schema_version: int = 2

    def validate(self) -> "WorkspaceConfig":
        if self.schema_version != 2:
            raise RequestValidationError(
                f"unsupported workspace schema version: {self.schema_version}"
            )
        values = tuple(
            process.validate(index)
            for index, process in enumerate(self.processes, start=1)
        )
        if not values:
            raise RequestValidationError(
                "workspace configuration must contain at least one process"
            )
        return WorkspaceConfig(values, 2)
