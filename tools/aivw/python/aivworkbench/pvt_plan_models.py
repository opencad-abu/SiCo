"""Immutable physical PVT planning value objects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .pvt_raw_output import raw_output_kind
from .pvt_paths import relative_path


@dataclass(frozen=True)
class PVTLaunchContext:
    """Validated inputs presented to one code-owned command builder."""

    contract: Mapping[str, Any]
    point: Mapping[str, Any]
    model_path: Path
    model_section: str
    deck_path: Path
    output_path: Path
    model_root: Path
    deck_root: Path
    payload_root: Path
    tools: Mapping[str, str]


@dataclass(frozen=True)
class PVTPointPlan:
    """One immutable, not-yet-invoked physical simulation point."""

    point_id: str
    coordinates: Mapping[str, Any]
    model_file: str
    model_section: str
    deck_path: Path
    output_path: Path
    command: tuple[str, ...]

    def expected_locator(self, payload_root: Path, producer: str) -> dict[str, object]:
        """Return a runtime expectation, not a publishable manifest locator."""
        raw_output = self.output_path.name
        return {
            "path": relative_path(payload_root, self.output_path),
            "kind": raw_output_kind(raw_output),
            "exists": False,
            "producer": producer,
            "point_id": self.point_id,
            "raw_output": raw_output,
        }


@dataclass(frozen=True)
class PVTLaunchPlan:
    """Result of adapter planning; ``READY`` still means execution not invoked."""

    status: str
    summary: Mapping[str, Any]
    points: tuple[PVTPointPlan, ...] = ()
    locators: tuple[Mapping[str, Any], ...] = ()

    def to_dict(
        self,
        payload_root: Path | None = None,
        *,
        deck_root: Path | None = None,
    ) -> dict[str, Any]:
        """Serialize a plan without confusing external deck inputs for payload artifacts."""

        root = payload_root
        deck_base = deck_root
        point_records: list[dict[str, Any]] = []
        for point in self.points:
            record: dict[str, Any] = {
                "id": point.point_id,
                "coordinates": dict(point.coordinates),
                "model_file": point.model_file,
                "model_section": point.model_section,
                "deck_path": str(point.deck_path),
                "output_path": str(point.output_path),
                "command": list(point.command),
            }
            if root is not None:
                record["output_relative"] = relative_path(root, point.output_path)
            if deck_base is not None:
                record["deck_relative"] = relative_path(deck_base, point.deck_path)
            point_records.append(record)
        return {
            "status": self.status,
            "summary": dict(self.summary),
            "points": point_records,
            "locators": [dict(item) for item in self.locators],
        }
