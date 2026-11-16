from __future__ import annotations

from copy import deepcopy
import json
import math

import pytest

from aivworkbench.l1 import build_l1_test_plan
from aivworkbench.l1_matrix import matrix_evidence, normalize_l1_matrix
from aivworkbench.recipe import load_builtin_recipe
from l1_matrix_fixtures import _cases, _matrix


def test_exact_cross_product_is_normalized_and_auditable() -> None:
    normalized = normalize_l1_matrix(_matrix(), _cases())
    assert normalized["expected_point_count"] == 16
    evidence = matrix_evidence(normalized, _cases())
    assert evidence["observed_point_count"] == 16
    assert evidence["coverage"] == 1.0
    assert len(evidence["points"]) == 16
@pytest.mark.parametrize(
    "mutate, message",
    (
        (lambda cases: cases.pop(), "exact cross product"),
        (lambda cases: cases.append(deepcopy(cases[0])), "duplicates"),
        (
            lambda cases: cases[0]["corner"].update({"voltage": 1.2}),
            "corner fields",
        ),
        (lambda cases: cases[0].pop("vector_id"), "dimension vector"),
        (lambda cases: cases[0]["corner"].update({"vdd_high": math.inf}), "finite"),
    ),
)
def test_matrix_rejects_incomplete_or_unsafe_observations(mutate, message: str) -> None:
    cases = _cases()
    mutate(cases)
    with pytest.raises(ValueError, match=message):
        normalize_l1_matrix(_matrix(), cases)
def test_matrix_rejects_out_of_contract_values_and_undeclared_metadata() -> None:
    cases = _cases()
    cases[0]["corner"]["process"] = "ff"
    with pytest.raises(ValueError, match="outside declared values"):
        normalize_l1_matrix(_matrix(), cases)

    cases = _cases()
    cases[0]["corner"]["extra"] = "unexpected"
    with pytest.raises(ValueError, match="corner fields"):
        normalize_l1_matrix(_matrix(), cases)

    no_vector = deepcopy(_matrix())
    no_vector["dimensions"] = no_vector["dimensions"][:-1]
    cases = _cases()
    with pytest.raises(ValueError, match="undeclared vector"):
        normalize_l1_matrix(no_vector, cases)
def test_matrix_rejects_duplicate_dimension_values_and_too_many_points() -> None:
    duplicate = _matrix()
    duplicate["dimensions"][0]["values"] = ["tt", "tt"]
    with pytest.raises(ValueError, match="values must be unique"):
        normalize_l1_matrix(duplicate, _cases())

    too_large = {
        "kind": "exact_cross_product",
        "require_exact_cross_product": True,
        "dimensions": [
            {
                "name": "x",
                "source": "corner",
                "application": "context",
                "values": list(range(65)),
            },
            {
                "name": "y",
                "source": "corner",
                "application": "context",
                "values": list(range(65)),
            },
        ],
    }
    with pytest.raises(ValueError, match="too many points"):
        normalize_l1_matrix(too_large, [])

    huge = {
        "kind": "exact_cross_product",
        "require_exact_cross_product": True,
        "dimensions": [
            {
                "name": "x",
                "source": "corner",
                "application": "context",
                "values": list(range(4096)),
            },
            {
                "name": "y",
                "source": "corner",
                "application": "context",
                "values": list(range(2)),
            },
        ],
    }
    with pytest.raises(ValueError, match="too many points"):
        normalize_l1_matrix(huge, [])

    bad_count = _matrix()
    bad_count["expected_point_count"] = True
    with pytest.raises(ValueError, match="expected_point_count must be an integer"):
        normalize_l1_matrix(bad_count, _cases())

    missing_count = _matrix()
    missing_count["expected_point_count"] = None
    with pytest.raises(ValueError, match="expected_point_count must be an integer"):
        normalize_l1_matrix(missing_count, _cases())
def test_matrix_numeric_keys_preserve_large_integer_coordinates() -> None:
    large_a = 10**400
    large_b = large_a + 1
    matrix = {
        "kind": "exact_cross_product",
        "require_exact_cross_product": True,
        "dimensions": [
            {
                "name": "code",
                "source": "corner",
                "application": "context",
                "values": [large_a, large_b],
            }
        ],
    }
    cases = [
        {"id": "large-a", "corner": {"code": large_a}},
        {"id": "large-b", "corner": {"code": large_b}},
    ]
    normalized = normalize_l1_matrix(matrix, cases)
    assert normalized["expected_point_count"] == 2
def test_matrix_rejects_whitespace_only_dimension_values() -> None:
    matrix = _matrix()
    matrix["dimensions"][0]["values"] = ["   ", "tt"]
    with pytest.raises(ValueError, match="text value must be non-empty"):
        normalize_l1_matrix(matrix, _cases())
def test_recipe_schema_matrix_values_require_scalar_nonblank_strings() -> None:
    from aivworkbench.profiles import product_root

    schema = json.loads(
        (product_root() / "schemas" / "recipe-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    values = schema["properties"]["verification"]["properties"]["l1"][
        "properties"
    ]["matrix"]["properties"]["dimensions"]["items"]["properties"]["values"]
    assert values["items"]["oneOf"] == [
        {"type": "string", "pattern": "\\S"},
        {"type": "number"},
    ]
def test_build_plan_keeps_legacy_recipe_without_matrix_unchanged() -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    plan = build_l1_test_plan(recipe.payload["verification"])
    assert "matrix" not in plan
    assert len(plan["cases"]) == 3
