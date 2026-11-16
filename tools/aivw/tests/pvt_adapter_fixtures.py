from __future__ import annotations

import itertools
from pathlib import Path

from aivworkbench.pvt_adapter import (
    PhysicalPVTLauncher, PhysicalPVTLauncherRegistry,
)

def _contract() -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "physical_pvt_exact_cross_product",
        "require_exact_cross_product": True,
        "dimensions": [
            {"name": "process", "source": "corner", "role": "process", "values": ["tt", "ss"]},
            {"name": "temperature_c", "source": "corner", "role": "temperature", "values": [27]},
            {"name": "vdd", "source": "corner", "role": "supply", "values": [1.2]},
            {"name": "vector", "source": "vector_id", "role": "vector", "values": ["positive", "negative"]},
        ],
        "model_binding": {
            "model_file": "models/device.scs",
            "section_dimension": "process",
            "section_by_value": {"tt": "tt", "ss": "slow"},
        },
        "execution": {
            "simulator": "spectre",
            "launcher": "spectre_pvt_test_v1",
            "raw_output": "psf",
            "payload_relative_root": "spectre/pvt",
        },
    }

def _cases() -> list[dict[str, object]]:
    result = []
    for index, (process, vector) in enumerate(itertools.product(("tt", "ss"), ("positive", "negative"))):
        result.append(
            {
                "id": f"pvt-{index:04d}",
                "corner": {"process": process, "temperature_c": 27, "vdd": 1.2},
                "vector_id": vector,
            }
        )
    return result

def _setup(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    model_root = tmp_path / "models"
    payload_root = tmp_path / "payload"
    deck_root = tmp_path / "decks"
    model_root.mkdir()
    payload_root.mkdir()
    deck_root.mkdir()
    (model_root / "models").mkdir()
    (model_root / "models" / "device.scs").write_text(
        "section tt {\n}\nsection slow {\n}\n", encoding="utf-8"
    )
    for case in _cases():
        (deck_root / f"{case['id']}.deck").write_text(
            f"// {case['id']}\n", encoding="utf-8"
        )
    spectre = tmp_path / "spectre"
    spectre.write_text("#!/bin/sh\n", encoding="utf-8")
    spectre.chmod(0o755)
    return model_root, payload_root, deck_root, {"spectre": str(spectre)}

def _registry(builder=None) -> PhysicalPVTLauncherRegistry:
    registry = PhysicalPVTLauncherRegistry()
    registry.register(
        PhysicalPVTLauncher(
            name="spectre_pvt_test_v1",
            version="contract-v1",
            simulator="spectre",
            raw_output="psf",
            required_tools=("spectre",),
            command_tool="spectre",
            command_builder=builder or (
                lambda context: (
                    context.tools["spectre"],
                    "-section",
                    context.model_section,
                    "-model",
                    str(context.model_path),
                    str(context.deck_path),
                    "-raw",
                    str(context.output_path),
                )
            ),
        )
    )
    return registry

def _file_registry(builder=None) -> PhysicalPVTLauncherRegistry:
    registry = PhysicalPVTLauncherRegistry()
    registry.register(
        PhysicalPVTLauncher(
            name="spectre_pvt_fsdb_test_v1",
            version="contract-v1",
            simulator="spectre",
            raw_output="fsdb",
            required_tools=("spectre",),
            command_tool="spectre",
            command_builder=builder or (
                lambda context: (
                    context.tools["spectre"],
                    "-section",
                    context.model_section,
                    "-model",
                    str(context.model_path),
                    str(context.deck_path),
                    "-fsdb",
                    str(context.output_path),
                )
            ),
        )
    )
    return registry

def _fsdb_contract() -> dict[str, object]:
    value = _contract()
    value["execution"] = {
        **value["execution"],
        "launcher": "spectre_pvt_fsdb_test_v1",
        "raw_output": "fsdb",
    }
    return value

def _materialize_observations(plan, payload_root: Path, statuses=("PASS",) * 4):
    observations = []
    for point, status in zip(plan.points, statuses):
        point.output_path.mkdir(parents=True)
        (point.output_path / "simulator.out").write_text(
            "raw evidence\n", encoding="utf-8"
        )
        observations.append(
            {
                "id": point.point_id,
                "coordinates": dict(point.coordinates),
                "model_section": point.model_section,
                "status": status,
                "raw_output": point.output_path.relative_to(payload_root).as_posix(),
                "metrics": {"value": 1.25},
            }
        )
    return observations

__all__ = ['_contract', '_cases', '_setup', '_registry', '_file_registry', '_fsdb_contract', '_materialize_observations']
