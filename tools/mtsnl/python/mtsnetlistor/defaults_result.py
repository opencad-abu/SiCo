"""Immutable raw report and editable effective defaults result values."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from .process import ProcessResult
from .model_design import SourceDesign
from .model_entries import ModelEntry
from .model_options import SimulatorOption
from .defaults_values import DEFAULTS_SCHEMA_VERSION, _normalise


@dataclass(frozen=True)
class DefaultsReport:
    status: str
    dialect: str
    tool_name: str
    source: Mapping[str, Any]
    baseline: Mapping[str, Any]
    after_design: Mapping[str, Any]
    after_startup_simrc: Mapping[str, Any]
    diagnostics: tuple[str, ...] = ()
    api_errors: tuple[str, ...] = ()
    schema_version: int = DEFAULTS_SCHEMA_VERSION
    run_dir: Path | None = None
    report_path: Path | None = None
    process: ProcessResult | None = None
    provider: str = "asi_initialization"
    mae_setup: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "status": self.status,
            "dialect": self.dialect,
            "tool_name": self.tool_name,
            "provider": self.provider,
            "source": _normalise(dict(self.source)),
            "baseline": _normalise(dict(self.baseline)),
            "after_design": _normalise(dict(self.after_design)),
            "after_startup_simrc": _normalise(dict(self.after_startup_simrc)),
            "diagnostics": list(self.diagnostics),
            "api_errors": list(self.api_errors),
        }
        if self.mae_setup is not None:
            payload["mae_setup"] = _normalise(dict(self.mae_setup))
        if self.run_dir is not None:
            payload["run_dir"] = str(self.run_dir)
        if self.report_path is not None:
            payload["report_path"] = str(self.report_path)
        if self.process is not None:
            payload["process"] = {
                "argv": list(self.process.argv),
                "returncode": self.process.returncode,
                "pid": self.process.pid,
                "pgid": self.process.pgid,
                "ppid": self.process.ppid,
                "started_at": self.process.started_at,
                "finished_at": self.process.finished_at,
                "cwd": self.process.cwd,
                "environment_digest": self.process.environment_digest,
                "timed_out": self.process.timed_out,
                "canceled": self.process.canceled,
                "lifetime": self.process.lifetime,
                "task_id": self.process.task_id,
                "worker_started_at": self.process.worker_started_at,
                "runtime_pid": self.process.runtime_pid,
            }
        return payload


PdkDefaultsResult = DefaultsReport


@dataclass(frozen=True)
class SourceDefaults:
    """User-facing effective defaults extracted from one provider snapshot."""

    provider: str
    dialect: str
    source: SourceDesign
    models: tuple[ModelEntry, ...]
    simulator_options: tuple[SimulatorOption, ...]
    temp_text: str = ""
    scale_text: str = ""
    diagnostics: tuple[str, ...] = ()
    run_dir: Path | None = None
    report_path: Path | None = None
    process: ProcessResult | None = None
    # Preserve the simulator's original spelling (for example ``1e-12``)
    # just like temperature and scale.  Appended after the existing fields so
    # positional construction of SourceDefaults remains source-compatible.
    gmin_text: str = ""

    @property
    def source_key(self) -> tuple[str, str, str]:
        return (self.source.library, self.source.cell, self.source.view)
