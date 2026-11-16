"""Sweep products, ambiguity rejection, model identity and stable nominal compatibility."""

import copy

import pytest
from simulation_dimensions_fixture import dimension_recipe
from simulation_recipe_fixture import recipe

from cadai.circuit_spec_schema import CircuitSpecError
from cadai.simulation_recipe import model_identities, native_recipe, preview_recipe


def test_cartesian_matrix_is_explicit_without_assumed_rdb_tuple_order():
    data = dimension_recipe()
    original = copy.deepcopy(data)
    result = preview_recipe(data)
    assert data == original
    assert len(result["expected_test_points"]) == 24
    assert len(result["sweep_combinations"]) == 4
    assert result["sweep_combinations"][2] == dict(RVAL=4000, CVAL=1e-9)
    native = native_recipe(data)
    assert native[4][1][:3] == ["ff", "-40", [["OFFSET", "1"]]]
    assert native[5][0] == ["RVAL", "2000 4000", [2000, 4000]]
    assert len(native_recipe(recipe())) == 4
    assert preview_recipe(recipe())["expected_test_points"] == [
        dict(test=t, corner="nominal", point=1) for t in ("settling", "bias")
    ]


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_corner",
        "reserved",
        "duplicate_axis",
        "undeclared",
        "local_override",
        "corner_overlap",
        "corner_undeclared",
        "corner_temperature",
        "models_mixed",
        "duplicate_model",
        "nan",
        "infinity",
        "duplicate_values",
        "near_values",
        "string_values",
        "single_value",
        "limit",
        "empty_corners",
    ],
)
def test_dimension_rejections(case):
    d = dimension_recipe()
    if case == "duplicate_corner":
        d["corners"][1]["name"] = "tt"
    elif case == "reserved":
        d["corners"][0]["name"] = "nominal"
    elif case == "duplicate_axis":
        d["sweeps"][1]["variable"] = "RVAL"
    elif case == "undeclared":
        d["sweeps"][0]["variable"] = "OTHER"
    elif case == "local_override":
        d["tests"][0]["variables"]["RVAL"] = "1K"
    elif case == "corner_overlap":
        d["corners"][0]["variables"]["RVAL"] = 1000
    elif case == "corner_undeclared":
        d["corners"][0]["variables"]["OTHER"] = 1
    elif case == "corner_temperature":
        d["corners"][0]["variables"]["temperature"] = 27
    elif case == "models_mixed":
        d["tests"][0]["models"] = [dict(path="/model.scs")]
    elif case == "duplicate_model":
        d["corners"][0]["models"] = [dict(path="/model.scs")] * 2
    elif case == "nan":
        d["sweeps"][0]["values"][0] = float("nan")
    elif case == "infinity":
        d["corners"][0]["variables"]["OFFSET"] = float("inf")
    elif case == "duplicate_values":
        d["sweeps"][0]["values"] = [1, 1]
    elif case == "near_values":
        d["sweeps"][0]["values"] = [1, 1 + 1e-14]
    elif case == "string_values":
        d["sweeps"][0]["values"] = ["1K", "2K"]
    elif case == "single_value":
        d["sweeps"][0]["values"] = [1]
    elif case == "limit":
        d["sweeps"][0]["values"] = list(range(16))
        d["sweeps"][1]["values"] = list(range(16))
    else:
        d["corners"] = []
    with pytest.raises(CircuitSpecError):
        preview_recipe(d)


def test_corner_only_files_participate_in_content_identity(tmp_path):
    path = tmp_path / "model.scs"
    path.write_text("parameters proof=1\n")
    d = dimension_recipe(path)
    before = model_identities(d)
    assert len(before) == 1
    path.write_text("parameters proof=2\n")
    assert before != model_identities(d)
    path.unlink()
    with pytest.raises(CircuitSpecError):
        model_identities(d)
