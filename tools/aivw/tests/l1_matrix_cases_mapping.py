from __future__ import annotations

from copy import deepcopy
import itertools
import json
import math

import pytest

from aivworkbench.plugins.latched_dynamic_comparator import (
    BaselineGenerationRequest,
    _matrix_instances,
    generate_baseline_candidate,
)
from aivworkbench.plugins.xcelium_evidence import (
    ADAPTER_NAME,
    get_xcelium_evidence_adapter,
    qualified_model_parameter_mapping,
    qualified_model_parameter_routes,
)
from aivworkbench.l1 import build_l1_test_plan
from aivworkbench.m1ai_inputs import load_contract
from aivworkbench.recipe import load_builtin_recipe
from l1_matrix_fixtures import _cases, _matrix


def test_comparator_adapter_maps_only_registered_numeric_model_parameters() -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    verification = deepcopy(recipe.payload["verification"])
    for index, case in enumerate(verification["cases"]):
        case["corner"] = {"vdd_high": 1.1 if index == 0 else 1.3}
        case["vector_id"] = "positive" if index == 0 else "negative"
    verification["l1"]["matrix"] = {
        "kind": "exact_cross_product",
        "require_exact_cross_product": True,
        "dimensions": [
            {
                "name": "vdd_high",
                "source": "corner",
                "application": "model_parameter",
                "values": [1.1, 1.3],
            },
            {
                "name": "vector",
                "source": "vector_id",
                "application": "stimulus",
                "values": ["positive", "negative"],
            },
        ],
    }
    # The original three cases cannot satisfy the four-point matrix; replace
    # them with the exact small product used for this renderer contract test.
    verification["cases"] = [
        {
            "id": f"m{index}",
            "type": "positive_differential" if vector == "positive" else "negative_differential",
            "inputs": {"Din+": 0.7 if vector == "positive" else 0.5, "Din-": 0.5 if vector == "positive" else 0.7},
            "expected": {"decision": vector, "out": vdd if vector == "positive" else 0.0, "outb": 0.0 if vector == "positive" else vdd},
            "assertions": ["decision", "out", "outb", "complementary"],
            "corner": {"vdd_high": vdd},
            "vector_id": vector,
        }
        for index, (vdd, vector) in enumerate(
            itertools.product((1.1, 1.3), ("positive", "negative"))
        )
    ]
    verification["l1"]["hold_case"] = "m0"
    contract = load_contract(recipe.input_path("interface_contract"))
    spec = json.loads(recipe.input_path("behavior_spec").read_text(encoding="utf-8"))
    candidate = generate_baseline_candidate(
        BaselineGenerationRequest(
            contract=contract,
            spec=spec,
            contract_sha256="a" * 64,
            spec_sha256="b" * 64,
            generated_at="test",
            verification=verification,
            model_options={},
        )
    )
    assert candidate.smoke_testbench.count("dut_matrix_") == 2
    assert candidate.smoke_testbench.count("#(.VDD_HIGH(") == 2
    assert "VDD_HIGH(1.1000000000000001)" in candidate.smoke_testbench
    assert "VDD_HIGH(1.3)" in candidate.smoke_testbench
    # Both vector cases at a given supply reuse the same model instance, while
    # the two supplies receive distinct parameter overrides.
    assert candidate.smoke_testbench.count("out_p_0000") > 2
    assert candidate.smoke_testbench.count("out_p_0001") > 2
def test_comparator_adapter_rejects_unqualified_model_parameter_dimension() -> None:
    matrix = _matrix()
    matrix["dimensions"][2]["name"] = "unknown_voltage"
    cases = _cases()
    for case in cases:
        case["corner"]["unknown_voltage"] = case["corner"].pop("vdd_high")
    plan = {"matrix": matrix, "cases": cases}
    from aivworkbench.plugins.xcelium_evidence import get_xcelium_evidence_adapter

    adapter = get_xcelium_evidence_adapter(
        "latched_dynamic_comparator.xcelium_evidence.v1"
    )
    assert adapter is not None
    findings = adapter.validate_plan(plan, adapter.binding_for_plan(plan))
    assert {
        item["code"] for item in findings
    } >= {"xcelium_evidence_matrix_model_parameter_unqualified"}
def test_comparator_adapter_exposes_only_controlled_parameter_mapping() -> None:
    assert qualified_model_parameter_mapping() == {
        "vdd_high": "VDD_HIGH",
        "vss_low": "VSS_LOW",
        "input_offset": "INPUT_OFFSET",
        "decision_epsilon": "DECISION_EPSILON",
    }
def test_comparator_matrix_maps_all_qualified_parameters_and_rejects_non_finite() -> None:
    plan = {
        "matrix": {
            "kind": "exact_cross_product",
            "require_exact_cross_product": True,
            "dimensions": [
                {
                    "name": name,
                    "source": "corner",
                    "application": "model_parameter",
                    "values": [value],
                }
                for name, value in (
                    ("vdd_high", 1.1),
                    ("vss_low", 0.05),
                    ("input_offset", 0.002),
                    ("decision_epsilon", 0.0001),
                )
            ],
        },
        "cases": [
            {
                "id": "all-parameters",
                "corner": {
                    "vdd_high": 1.1,
                    "vss_low": 0.05,
                    "input_offset": 0.002,
                    "decision_epsilon": 0.0001,
                },
            }
        ],
    }
    parameters = {
        name: {"value": value}
        for name, value in (
            ("vdd_high", 1.2),
            ("vss_low", 0.0),
            ("input_offset", 0.0),
            ("decision_epsilon", 0.001),
        )
    }
    instances = _matrix_instances(plan, parameters)
    assert instances is not None
    assert instances["instances"] == [
        {
            "key": "1.1000000000000001|0.050000000000000003|0.002|0.0001",
            "suffix": "0000",
            "name": "dut_matrix_0000",
            "overrides": {
                "VDD_HIGH": 1.1,
                "VSS_LOW": 0.05,
                "INPUT_OFFSET": 0.002,
                "DECISION_EPSILON": 0.0001,
            },
        }
    ]

    plan["cases"][0]["corner"]["input_offset"] = math.inf
    with pytest.raises(ValueError, match="must be finite"):
        _matrix_instances(plan, parameters)
def test_comparator_matrix_rejects_integer_coordinates_that_collapse_in_sv_real() -> None:
    large_a = 2**53
    large_b = large_a + 1
    matrix = {
        "kind": "exact_cross_product",
        "require_exact_cross_product": True,
        "dimensions": [
            {
                "name": "vdd_high",
                "source": "corner",
                "application": "model_parameter",
                "values": [large_a, large_b],
            }
        ],
    }
    plan = {
        "matrix": matrix,
        "cases": [
            {"id": "large-a", "corner": {"vdd_high": large_a}},
            {"id": "large-b", "corner": {"vdd_high": large_b}},
        ],
    }
    with pytest.raises(
        ValueError, match="cannot be represented exactly as SV real"
    ):
        qualified_model_parameter_routes(plan)
def test_comparator_adapter_reports_unrepresentable_matrix_parameter() -> None:
    large = 10**400
    plan = {
        "matrix": {
            "kind": "exact_cross_product",
            "require_exact_cross_product": True,
            "dimensions": [
                {
                    "name": "vdd_high",
                    "source": "corner",
                    "application": "model_parameter",
                    "values": [large],
                }
            ],
        },
        "cases": [{"id": "large", "corner": {"vdd_high": large}}],
        "coverage": None,
        "waveform": None,
    }
    adapter = get_xcelium_evidence_adapter(ADAPTER_NAME)
    assert adapter is not None
    findings = adapter.validate_plan(plan, {})
    assert "xcelium_evidence_matrix_invalid" in {
        item["code"] for item in findings
    }
@pytest.mark.parametrize("invalid_matrix", ([], "matrix", 1))
def test_comparator_matrix_rejects_non_object_contract(invalid_matrix: object) -> None:
    with pytest.raises(ValueError, match="matrix contract must be an object"):
        _matrix_instances({"matrix": invalid_matrix, "cases": []}, {})
def test_qualification_recipe_has_authoritative_16_point_matrix_and_two_instances() -> None:
    recipe = load_builtin_recipe("qualification.comparator_raw_evidence_matrix")
    plan = build_l1_test_plan(recipe.payload["verification"])
    assert plan["matrix"]["expected_point_count"] == 16
    assert len(plan["cases"]) == 16
    assert plan["coverage"]["required_points"][-1] == "matrix-15"
    # Four context/vector points share each of the two vdd_high model values.
    spec = json.loads(recipe.input_path("behavior_spec").read_text(encoding="utf-8"))
    instances = _matrix_instances(plan, spec["parameters"])
    assert instances is not None
    assert [item["name"] for item in instances["instances"]] == [
        "dut_matrix_0000",
        "dut_matrix_0001",
    ]
    assert recipe.payload["model"]["xcelium_evidence_adapter"] == (
        "latched_dynamic_comparator.xcelium_evidence.v1"
    )
