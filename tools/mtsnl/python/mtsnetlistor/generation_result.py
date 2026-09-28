"""Immutable one-cell and ordered multi-cell generation outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from .model_request import NetlistRequest
from .process import ProcessResult
from .serialize import json_value


@dataclass(frozen=True)
class GenerationResult:
    request_digest: str
    status: str
    run_dir: Path
    raw_netlist: Path
    scoped_netlist: Path
    parser_report: Path
    stable_output: Path
    process: ProcessResult
    corner_profiles: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "request_digest": self.request_digest,
            "status": self.status,
            "run_dir": str(self.run_dir),
            "raw_netlist": str(self.raw_netlist),
            "scoped_netlist": str(self.scoped_netlist),
            "parser_report": str(self.parser_report),
            "stable_output": str(self.stable_output),
            "process": json_value(self.process),
            "corner_profiles": list(self.corner_profiles),
        }


@dataclass(frozen=True)
class CellGenerationResult:
    """One cell result returned by :func:`generate_many`."""

    request: NetlistRequest
    result: GenerationResult

    @property
    def cell(self) -> str:
        return self.request.source.cell

    def to_dict(self) -> dict[str, object]:
        return {
            "cell": self.cell,
            "request_digest": self.result.request_digest,
            "generation": self.result.to_dict(),
        }


@dataclass(frozen=True)
class MultiGenerationResult:
    """Ordered results for a multi-cell generation request."""

    status: str
    cells: tuple[CellGenerationResult, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": self.status,
            "cells": [item.to_dict() for item in self.cells],
        }
