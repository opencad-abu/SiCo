from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from aivworkbench.errors import RecipeError
from aivworkbench.profiles import product_root
from aivworkbench.recipe import available_recipes, load_builtin_recipe, load_recipe
from aivworkbench.registry import PluginRegistry, PluginSpec, builtin_registry
from aivworkbench.l1 import build_l1_test_plan


def _payload() -> dict[str, object]:
    return copy.deepcopy(dict(load_builtin_recipe("saradc.comparator_new").payload))


def _write_recipe(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def test_builtin_comparator_recipe_is_generic_contract_input() -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    assert "saradc.comparator_new" in available_recipes()
    assert recipe.revision == 3
    assert recipe.model_class == "latched_dynamic_comparator"
    assert recipe.target["output_view"] == {
        "name": "rnm",
        "view_type": "systemVerilogText",
        "language": "systemverilog",
        "primary_file": "verilog.sv",
    }
    assert recipe.input_path("behavior_spec").name == "comparator_new_rnm.json"
    assert recipe.payload["model"]["generation"] == "ai_iterative"
    assert recipe.payload["model"]["template_policy"] == "reference_only"


def test_recipe_schema_document_is_valid_json() -> None:
    schema = json.loads(
        (product_root() / "schemas" / "recipe-v1.schema.json").read_text(encoding="utf-8")
    )
    assert schema["properties"]["schema_version"]["const"] == 1
    assert schema["additionalProperties"] is False


def test_manifest_schema_document_is_valid_json() -> None:
    schema = json.loads(
        (product_root() / "schemas" / "manifest-v1.schema.json").read_text(encoding="utf-8")
    )
    assert schema["properties"]["schema_version"]["const"] == 1
    assert "binding" in schema["required"]


def test_recipe_rejects_unknown_executor(tmp_path: Path) -> None:
    payload = _payload()
    payload["workflow"]["gates"][0]["executor"] = "example.hardcoded_runner"
    with pytest.raises(RecipeError, match="unknown executor"):
        load_recipe(_write_recipe(tmp_path / "recipe.json", payload))


def test_recipe_rejects_cyclic_gate_graph(tmp_path: Path) -> None:
    payload = _payload()
    payload["workflow"]["gates"][0]["needs"] = ["publish"]
    with pytest.raises(RecipeError, match="cycle"):
        load_recipe(_write_recipe(tmp_path / "recipe.json", payload))


def test_recipe_rejects_path_escape(tmp_path: Path) -> None:
    payload = _payload()
    payload["inputs"]["behavior_spec"] = "recipe:../outside.json"
    with pytest.raises(RecipeError, match="unsafe input reference"):
        load_recipe(_write_recipe(tmp_path / "recipe.json", payload))


def test_config_backed_recipe_requires_snapshot_bound_config_gate(tmp_path: Path) -> None:
    payload = _payload()
    payload["target"]["source_views"]["config"] = "config"
    with pytest.raises(RecipeError, match="config-backed recipes require exactly one"):
        load_recipe(_write_recipe(tmp_path / "config-without-gate.json", payload))


@pytest.mark.parametrize(
    "value, message",
    (
        ("unknown.adapter", "unknown model.xcelium_evidence_adapter"),
        ("", "model.xcelium_evidence_adapter must be non-empty text"),
        (" bad ", "model.xcelium_evidence_adapter must be non-empty text"),
    ),
)
def test_recipe_rejects_unknown_or_malformed_evidence_adapter(
    tmp_path: Path, value: object, message: str
) -> None:
    payload = _payload()
    payload["model"]["xcelium_evidence_adapter"] = value
    with pytest.raises(RecipeError, match=message):
        load_recipe(_write_recipe(tmp_path / "recipe.json", payload))


def test_recipe_rejects_invalid_l1_case_contract(tmp_path: Path) -> None:
    payload = _payload()
    payload["verification"]["cases"][1]["id"] = "nominal-positive"
    with pytest.raises(RecipeError, match="case IDs must be unique"):
        load_recipe(_write_recipe(tmp_path / "duplicate-case.json", payload))

    payload = _payload()
    payload["verification"]["l1"]["required_metrics"] = ["missing"]
    with pytest.raises(RecipeError, match="required_metrics"):
        load_recipe(_write_recipe(tmp_path / "unknown-metric.json", payload))

    payload = _payload()
    payload["verification"]["l1"]["minimum_case_coverage"] = 1.1
    with pytest.raises(RecipeError, match="minimum_case_coverage"):
        load_recipe(_write_recipe(tmp_path / "bad-coverage.json", payload))

    payload = _payload()
    payload["verification"]["l1"]["temporal_assertions"] = [
        {"id": "same", "property": "a", "clock": "posedge clk"},
        {"id": "same", "property": "b", "clock": "posedge clk"},
    ]
    with pytest.raises(RecipeError, match="temporal assertion IDs must be unique"):
        load_recipe(_write_recipe(tmp_path / "duplicate-temporal.json", payload))

    payload = _payload()
    payload["verification"]["l1"]["temporal_assertions"] = [
        {"id": "missing-property", "clock": "posedge clk"}
    ]
    with pytest.raises(RecipeError, match="property"):
        load_recipe(_write_recipe(tmp_path / "missing-temporal-property.json", payload))


def test_l1_plan_preserves_case_metadata_and_temporal_contract() -> None:
    payload = _payload()
    payload["verification"]["cases"][0]["corner"] = {
        "process": "tt",
        "vdd": 1.2,
        "temperature_c": 27,
    }
    payload["verification"]["cases"][0]["vector_id"] = "nominal-tt-001"
    payload["verification"]["l1"]["temporal_assertions"] = [
        {
            "id": "output_complementary",
            "property": "out + outb == VDD",
            "clock": "posedge clk",
            "severity": "error",
        }
    ]
    plan = build_l1_test_plan(payload["verification"])
    assert plan["cases"][0]["corner"] == {
        "process": "tt",
        "vdd": 1.2,
        "temperature_c": 27,
    }
    assert plan["cases"][0]["vector_id"] == "nominal-tt-001"
    assert plan["temporal_assertions"] == [
        {
            "id": "output_complementary",
            "property": "out + outb == VDD",
            "clock": "posedge clk",
            "severity": "error",
        }
    ]


def test_l1_plan_preserves_coverage_and_waveform_contract(tmp_path: Path) -> None:
    payload = _payload()
    payload["verification"]["l1"]["coverage"] = {
        "id": "functional",
        "backend": "ucis",
        "minimum_score": 0.9,
        "required_points": ["positive", "negative"],
    }
    payload["verification"]["l1"]["waveform"] = {
        "id": "debug-wave",
        "format": "shm",
        "retention": "on_failure",
    }
    plan = build_l1_test_plan(payload["verification"])
    assert plan["coverage"] == {
        "id": "functional",
        "backend": "ucis",
        "minimum_score": 0.9,
        "required_points": ["positive", "negative"],
    }
    assert plan["waveform"] == {
        "id": "debug-wave",
        "format": "shm",
        "retention": "on_failure",
    }

    payload["verification"]["l1"]["coverage"]["minimum_score"] = 1.1
    with pytest.raises(RecipeError, match="coverage.minimum_score"):
        load_recipe(_write_recipe(tmp_path / "bad-coverage-contract.json", payload))


def test_registry_rejects_duplicate_plugins_and_describes_capabilities() -> None:
    registry = PluginRegistry()
    spec = PluginSpec("executor", "1", frozenset({"test"}))
    registry.register_executor(spec)
    with pytest.raises(RecipeError, match="duplicate executor"):
        registry.register_executor(spec)
    builtins = builtin_registry().describe()
    assert any(item["name"] == "virtuoso.publish_text_view" for item in builtins["executors"])


def test_registry_binds_handlers_without_changing_plugin_identity() -> None:
    def handler(_context: object) -> object:
        return object()

    registry = builtin_registry(executor_handlers={"xcelium.rnm_check": handler})
    spec = registry.require_executor("xcelium.rnm_check")
    assert spec.version == "contract-v1"
    assert spec.handler is handler
    with pytest.raises(RecipeError, match="already has a handler"):
        registry.bind_executor("xcelium.rnm_check", handler)
