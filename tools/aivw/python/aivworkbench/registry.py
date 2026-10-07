"""Versioned executor and model-class registry for recipe validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from .errors import RecipeError


@dataclass(frozen=True)
class PluginSpec:
    name: str
    version: str
    capabilities: frozenset[str]
    handler: Callable[..., object] | None = None


class PluginRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, PluginSpec] = {}
        self._model_classes: dict[str, PluginSpec] = {}

    def register_executor(self, spec: PluginSpec) -> None:
        self._register(self._executors, spec, "executor")

    def register_model_class(self, spec: PluginSpec) -> None:
        self._register(self._model_classes, spec, "model class")

    def bind_executor(self, name: str, handler: Callable[..., object]) -> None:
        current = self.require_executor(name)
        if current.handler is not None:
            raise RecipeError(f"executor already has a handler: {name}")
        if not callable(handler):
            raise RecipeError(f"executor handler is not callable: {name}")
        self._executors[name] = PluginSpec(
            name=current.name,
            version=current.version,
            capabilities=current.capabilities,
            handler=handler,
        )

    def bind_model_class(self, name: str, handler: Callable[..., object]) -> None:
        current = self.require_model_class(name)
        if current.handler is not None:
            raise RecipeError(f"model class already has a handler: {name}")
        if not callable(handler):
            raise RecipeError(f"model-class handler is not callable: {name}")
        self._model_classes[name] = PluginSpec(
            name=current.name,
            version=current.version,
            capabilities=current.capabilities,
            handler=handler,
        )

    def require_executor(self, name: str) -> PluginSpec:
        return self._require(self._executors, name, "executor")

    def require_model_class(self, name: str) -> PluginSpec:
        return self._require(self._model_classes, name, "model class")

    def describe(self) -> dict[str, list[dict[str, object]]]:
        return {
            "executors": self._describe(self._executors.values()),
            "model_classes": self._describe(self._model_classes.values()),
        }

    @staticmethod
    def _register(values: dict[str, PluginSpec], spec: PluginSpec, kind: str) -> None:
        if not spec.name or not spec.version or not spec.capabilities:
            raise RecipeError(f"{kind} registration is incomplete: {spec!r}")
        if spec.name in values:
            raise RecipeError(f"duplicate {kind} registration: {spec.name}")
        values[spec.name] = spec

    @staticmethod
    def _require(values: dict[str, PluginSpec], name: str, kind: str) -> PluginSpec:
        try:
            return values[name]
        except KeyError as exc:
            raise RecipeError(f"unknown {kind}: {name}") from exc

    @staticmethod
    def _describe(values: Iterable[PluginSpec]) -> list[dict[str, object]]:
        return [
            {
                "name": item.name,
                "version": item.version,
                "capabilities": sorted(item.capabilities),
                "executable": item.handler is not None,
            }
            for item in sorted(values, key=lambda entry: entry.name)
        ]


def builtin_registry(
    *,
    executor_handlers: Mapping[str, Callable[..., object]] | None = None,
    model_class_handlers: Mapping[str, Callable[..., object]] | None = None,
) -> PluginRegistry:
    registry = PluginRegistry()
    for name, capabilities in (
        ("virtuoso.snapshot", {"oa", "ipc", "read"}),
        ("virtuoso.config_binding", {"oa", "ipc", "read", "config", "ams"}),
        ("cadence.si", {"ipc", "read", "structure", "systemverilog"}),
        ("connectivity.check", {"read", "structure", "systemverilog"}),
        ("ai.generate_model", {"ai", "systemverilog", "veriloga"}),
        ("xcelium.rnm_check", {"compile", "elaborate", "systemverilog"}),
        ("spectre.golden_evidence", {"spectre", "scalar", "waveform"}),
        ("ldo.experiment_contract", {"read", "contract", "spectre"}),
        ("spectre.measurements", {"read", "scalar", "waveform"}),
        ("spectre.exploratory", {"read", "spectre", "scalar", "waveform", "exploration"}),
        ("spectre.block_characterization", {"read", "spectre", "scalar", "waveform"}),
        ("electrical.trajectory_measurements", {"read", "waveform", "timing", "current"}),
        ("evidence.import_characterization", {"read", "public-evidence"}),
        ("electrical.identify_dynamics", {"read", "system-identification", "systemverilog"}),
        ("xcelium.trajectory_check", {"compile", "elaborate", "waveform", "systemverilog"}),
        ("evidence.trajectory_correlation", {"read", "waveform", "timing", "current"}),
        ("evidence.correlate", {"metric", "statistical", "waveform"}),
        ("virtuoso.publish_text_view", {"ipc", "systemverilog", "veriloga", "write"}),
    ):
        registry.register_executor(
            PluginSpec(
                name=name, version="contract-v1", capabilities=frozenset(capabilities)
            )
        )
    registry.register_model_class(
        PluginSpec(
            name="latched_dynamic_comparator",
            version="1",
            capabilities=frozenset(
                {"clocked", "decision-region", "differential", "rnm"}
            ),
        )
    )
    registry.register_model_class(
        PluginSpec(
            name="ldo.linear_regulator",
            version="1",
            capabilities=frozenset(
                {"dc-transfer", "supply-window", "optional-enable", "transient", "sampled-rnm"}
            ),
        )
    )
    registry.register_model_class(PluginSpec("electrical.sampled_state", "1",
        frozenset({"system-identification", "sampled-rnm", "continuous-input", "diagnostic-current"})))
    if executor_handlers is None:
        from .executors.cadence_si import run_cadence_si
        from .executors.connectivity import run_connectivity_check
        from .executors.model_generation import run_model_generation
        from .executors.virtuoso import run_config_binding, run_snapshot
        from .executors.xcelium import run_rnm_check
        from .executors.ldo_contract import run_ldo_contract
        from .executors.spectre_golden import run_spectre_golden
        from .executors.spectre_measurements import run_spectre_measurements
        from .executors.spectre_exploratory import run_spectre_exploratory
        from .executors.spectre_block import run_block_characterization
        from .executors.trajectory_measurements import run_trajectory_measurements
        from .executors.characterization_import import run_characterization_import
        from .executors.trajectory_model import run_trajectory_model
        from .executors.trajectory_xcelium import run_trajectory_xcelium
        from .executors.trajectory_correlation import run_trajectory_correlation

        executor_handlers = {
            "virtuoso.snapshot": run_snapshot,
            "virtuoso.config_binding": run_config_binding,
            "cadence.si": run_cadence_si,
            "connectivity.check": run_connectivity_check,
            "ai.generate_model": run_model_generation,
            "xcelium.rnm_check": run_rnm_check,
            "ldo.experiment_contract": run_ldo_contract,
            "spectre.golden_evidence": run_spectre_golden,
            "spectre.measurements": run_spectre_measurements,
            "spectre.exploratory": run_spectre_exploratory,
            "spectre.block_characterization": run_block_characterization,
            "electrical.trajectory_measurements": run_trajectory_measurements,
            "evidence.import_characterization": run_characterization_import,
            "electrical.identify_dynamics": run_trajectory_model,
            "xcelium.trajectory_check": run_trajectory_xcelium,
            "evidence.trajectory_correlation": run_trajectory_correlation,
        }
    if model_class_handlers is None:
        from .plugins.latched_dynamic_comparator import generate_baseline_candidate
        from .plugins.linear_regulator import generate_linear_regulator_candidate
        from .plugins.sampled_state import SampledStatePlugin

        model_class_handlers = {
            "latched_dynamic_comparator": generate_baseline_candidate,
            "ldo.linear_regulator": generate_linear_regulator_candidate,
            "electrical.sampled_state": SampledStatePlugin(),
        }
    for name, handler in (executor_handlers or {}).items():
        registry.bind_executor(name, handler)
    for name, handler in (model_class_handlers or {}).items():
        registry.bind_model_class(name, handler)
    return registry


__all__ = ["PluginRegistry", "PluginSpec", "builtin_registry"]
