from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from aivworkbench.executor import ExecutorContext, ExecutorResult
from aivworkbench.l1 import build_l1_test_plan
from aivworkbench.plugins.xcelium_evidence import (
    ADAPTER_NAME,
    ComparatorXceliumEvidenceAdapter,
    get_xcelium_evidence_adapter,
)
from aivworkbench.recipe import Recipe, load_builtin_recipe
from aivworkbench.workspace import (
    allocate_split_run,
    resolve_launch_paths,
    stable_digest,
)


def _plan(
    *,
    backend: str = "ucis",
    waveform_format: str = "shm",
    retention: str = "always",
) -> dict[str, object]:
    plan = deepcopy(
        build_l1_test_plan(
            load_builtin_recipe("saradc.comparator_new").payload["verification"]
        )
    )
    points = [*plan["required_checks"], *(item["id"] for item in plan["cases"])]
    plan["coverage"] = {
        "id": "functional",
        "backend": backend,
        "minimum_score": 1.0,
        "required_points": points,
    }
    plan["waveform"] = {
        "id": "debug-wave",
        "format": waveform_format,
        "retention": retention,
    }
    return plan


def _adapter() -> ComparatorXceliumEvidenceAdapter:
    adapter = get_xcelium_evidence_adapter(ADAPTER_NAME)
    assert adapter is not None
    return adapter


def _run(tmp_path: Path):
    launch = tmp_path / "launch"
    payload = tmp_path / "payload"
    launch.mkdir()
    payload.mkdir()
    return allocate_split_run(
        resolve_launch_paths(launch),
        payload,
        library="qualification",
        cell="comparator",
        view="rnm",
        kind="recipe",
        request_digest=stable_digest({"test": "xcelium-evidence"}),
    )


def _recipe_with_plan(recipe: Recipe, plan: dict[str, object]) -> Recipe:
    payload = deepcopy(dict(recipe.payload))
    payload["verification"]["l1"]["coverage"] = deepcopy(plan["coverage"])
    payload["verification"]["l1"]["waveform"] = deepcopy(plan["waveform"])
    payload["model"]["xcelium_evidence_adapter"] = ADAPTER_NAME
    return Recipe(recipe.path, payload, recipe.sha256, recipe.resolved_inputs)


def _executor_context(
    tmp_path: Path,
    *,
    backend: str = "ucis",
    tools: dict[str, str] | None = None,
) -> ExecutorContext:
    recipe = load_builtin_recipe("saradc.comparator_new")
    plan = _plan(backend=backend)
    recipe = _recipe_with_plan(recipe, plan)
    run = _run(tmp_path)
    binding = _adapter().binding_for_plan(plan)
    context = ExecutorContext(
        recipe=recipe,
        run=run,
        gate_id="model_check",
        executor_name="xcelium.rnm_check",
        dependencies={
            "generate": ExecutorResult(
                "PASS",
                outputs={
                    "model_source": "missing-model.sv",
                    "smoke_testbench": "missing-tb.sv",
                    "pass_marker": "AIVW_RNM_SMOKE checks=5 PASS",
                    "l1_test_plan": plan,
                    "xcelium_evidence_binding": binding,
                    "xcelium_evidence_adapter": ADAPTER_NAME,
                },
            )
        },
        environment={},
        tools=tools or {},
        timeout=30,
    )
    context.gate_root.mkdir(parents=True)
    return context


def _event(kind: str, identifier: str, path: str) -> dict[str, object]:
    return {
        "event": kind,
        "id": identifier,
        "status": "PASS",
        "path": path,
    }
