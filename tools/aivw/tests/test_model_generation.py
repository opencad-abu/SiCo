from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aivworkbench.executor import ExecutorContext, ExecutorResult
from aivworkbench.executors.model_generation import run_model_generation
from aivworkbench.plugins.latched_dynamic_comparator import (
    BaselineGenerationRequest,
    generate_baseline_candidate,
)
from aivworkbench.l1 import build_l1_test_plan
from aivworkbench.recipe import load_builtin_recipe
from aivworkbench.registry import builtin_registry
from aivworkbench.workspace import (
    allocate_split_run,
    resolve_launch_paths,
    stable_digest,
)


def test_generation_plugin_cannot_mutate_recipe_owned_l1_plan(tmp_path: Path) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    original_verification = deepcopy(recipe.payload["verification"])

    def mutating_plugin(request):
        request.verification["cases"].pop()
        return SimpleNamespace(
            source="module comparator_new; endmodule\n",
            smoke_testbench="module tb; endmodule\n",
            model_version="malicious-test",
            test_plan=build_l1_test_plan(request.verification),
        )

    registry = builtin_registry(
        executor_handlers={},
        model_class_handlers={"latched_dynamic_comparator": mutating_plugin},
    )
    launch = tmp_path / "launch"
    payload = tmp_path / "payload"
    launch.mkdir()
    payload.mkdir()
    run = allocate_split_run(
        resolve_launch_paths(launch),
        payload,
        library="lib",
        cell="cell",
        view="rnm",
        kind="recipe",
        request_digest=stable_digest({"test": "mutating-plugin"}),
    )
    context = ExecutorContext(
        recipe=recipe,
        run=run,
        gate_id="generate",
        executor_name="ai.generate_model",
        dependencies={
            "structure": ExecutorResult(
                "PASS",
                outputs={
                    "structure_authoritative": True,
                    "source_generation": "a" * 64,
                },
            )
        },
        environment={},
        tools={},
        timeout=30,
        metadata={"registry": registry},
    )
    context.gate_root.mkdir(parents=True)

    result = run_model_generation(context)

    assert result.status == "BLOCKED_INPUT"
    assert result.summary["code"] == "generation_input_invalid"
    assert "changed the recipe-owned L1 test plan" in result.summary["detail"]
    assert recipe.payload["verification"] == original_verification
    assert not (run.payload_root / "models").exists()


def test_comparator_plugin_blocks_unbound_temporal_assertions() -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    verification = deepcopy(recipe.payload["verification"])
    verification["l1"]["temporal_assertions"] = [
        {"id": "output_complementary", "property": "out + outb == VDD", "clock": "posedge clk"}
    ]
    contract = json.loads(
        recipe.input_path("interface_contract").read_text(encoding="utf-8")
    )
    spec = json.loads(
        recipe.input_path("behavior_spec").read_text(encoding="utf-8")
    )
    request = BaselineGenerationRequest(
        contract=contract,
        spec=spec,
        contract_sha256="a" * 64,
        spec_sha256="b" * 64,
        generated_at="2026-01-01T00:00:00+00:00",
        verification=verification,
    )
    with pytest.raises(ValueError, match="does not yet render temporal assertions"):
        generate_baseline_candidate(request)


@pytest.mark.parametrize(
    "field, value, message",
    (
        (
            "coverage",
            {"id": "functional", "backend": "ucis", "minimum_score": 0.9, "required_points": []},
            "does not yet render coverage evidence",
        ),
        (
            "waveform",
            {"id": "debug-wave", "format": "shm", "retention": "always"},
            "does not yet retain waveform evidence",
        ),
    ),
)
def test_comparator_plugin_blocks_unbound_l1_artifact_contracts(
    field: str, value: dict[str, object], message: str
) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    verification = deepcopy(recipe.payload["verification"])
    verification["l1"][field] = value
    contract = json.loads(recipe.input_path("interface_contract").read_text(encoding="utf-8"))
    spec = json.loads(recipe.input_path("behavior_spec").read_text(encoding="utf-8"))
    request = BaselineGenerationRequest(
        contract=contract,
        spec=spec,
        contract_sha256="a" * 64,
        spec_sha256="b" * 64,
        generated_at="2026-01-01T00:00:00+00:00",
        verification=verification,
    )
    with pytest.raises(ValueError, match=message):
        generate_baseline_candidate(request)


def test_generation_candidate_may_declare_xcelium_evidence_binding() -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    contract = json.loads(recipe.input_path("interface_contract").read_text(encoding="utf-8"))
    spec = json.loads(recipe.input_path("behavior_spec").read_text(encoding="utf-8"))
    request = BaselineGenerationRequest(
        contract=contract,
        spec=spec,
        contract_sha256="a" * 64,
        spec_sha256="b" * 64,
        generated_at="2026-01-01T00:00:00+00:00",
        verification=recipe.payload["verification"],
    )
    candidate = generate_baseline_candidate(request)
    assert candidate.xcelium_evidence_binding is None


def test_generation_candidate_selects_registered_xcelium_evidence_adapter() -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    contract = json.loads(recipe.input_path("interface_contract").read_text(encoding="utf-8"))
    spec = json.loads(recipe.input_path("behavior_spec").read_text(encoding="utf-8"))
    verification = deepcopy(recipe.payload["verification"])
    verification["l1"]["coverage"] = {
        "id": "functional",
        "backend": "ucis",
        "minimum_score": 1.0,
        "required_points": [
            "initial-state",
            "clock-hold",
            "nominal-positive",
            "nominal-negative",
            "nominal-zero",
        ],
    }
    verification["l1"]["waveform"] = {
        "id": "debug-wave",
        "format": "shm",
        "retention": "always",
    }
    request = BaselineGenerationRequest(
        contract=contract,
        spec=spec,
        contract_sha256="a" * 64,
        spec_sha256="b" * 64,
        generated_at="2026-01-01T00:00:00+00:00",
        verification=verification,
        model_options={
            "xcelium_evidence_adapter": "latched_dynamic_comparator.xcelium_evidence.v1"
        },
    )
    candidate = generate_baseline_candidate(request)
    assert candidate.xcelium_evidence_adapter == (
        "latched_dynamic_comparator.xcelium_evidence.v1"
    )
    assert candidate.xcelium_evidence_binding == {
        "coverage": {
            "id": "functional",
            "path": "evidence/coverage",
            "producer": "xcelium",
        },
        "waveform": {
            "id": "debug-wave",
            "path": "evidence/waves.shm",
            "producer": "xcelium",
        },
    }
    assert '\\"event\\":\\"coverage\\"' in candidate.smoke_testbench
    assert "evidence/waves.shm" in candidate.smoke_testbench


@pytest.mark.parametrize(
    "value, message",
    (
        ("unknown.adapter", "unknown xcelium evidence adapter"),
        ("", "xcelium_evidence_adapter must be non-empty text"),
        (" bad ", "xcelium_evidence_adapter must be non-empty text"),
        (None, "xcelium_evidence_adapter must be non-empty text"),
    ),
)
def test_generation_candidate_rejects_unknown_or_malformed_adapter_options(
    value: object, message: str
) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    contract = json.loads(recipe.input_path("interface_contract").read_text(encoding="utf-8"))
    spec = json.loads(recipe.input_path("behavior_spec").read_text(encoding="utf-8"))
    request = BaselineGenerationRequest(
        contract=contract,
        spec=spec,
        contract_sha256="a" * 64,
        spec_sha256="b" * 64,
        generated_at="2026-01-01T00:00:00+00:00",
        verification=recipe.payload["verification"],
        model_options={"xcelium_evidence_adapter": value},
    )
    with pytest.raises(ValueError, match=message):
        generate_baseline_candidate(request)
