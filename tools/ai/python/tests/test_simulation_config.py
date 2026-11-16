"""Explicit Config references and lists stay visible through preview and transport."""

import copy

import pytest
from simulation_recipe_fixture import recipe
from test_circuit_config import BIND, SPEC

from cadai.circuit_config import build_config_skill, checked_config
from cadai.circuit_spec_schema import CircuitSpecError
from cadai.circuit_tools import CircuitArgumentError
from cadai.simulation_recipe import native_recipe, preview_recipe


def test_config_reference_is_preserved_and_old_recipe_payload_stays_compatible():
    data = recipe()
    old = native_recipe(data)
    assert all(len(t) == 9 for t in old[3])
    data["tests"][0]["config_ref"] = "config:my_config"
    data["tests"][0]["design"]["view"] = "cfg_typical"
    result = preview_recipe(data)
    assert result["recipe"]["tests"][0]["config_ref"] == "config:my_config"
    assert native_recipe(data)[3][0][9] == "config:my_config"
    assert result["recipe_digest"] != preview_recipe(recipe())["recipe_digest"]
    assert len(native_recipe(data)[3][1]) == 9


@pytest.mark.parametrize("ref", ["", None, False, "config:x)exit(", "unmanaged:path"])
def test_invalid_config_reference_rejected_before_dispatch(ref):
    data = recipe()
    data["tests"][0]["config_ref"] = ref
    with pytest.raises(CircuitSpecError):
        preview_recipe(data)


def test_custom_schematic_view_and_explicit_ordered_lists():
    args = {
        **SPEC,
        "bindings": [{**BIND, "view": "schematic_alt"}],
        "switch_views": ["spectre", "schematic_alt", "schematic"],
        "stop_views": ["spectre"],
    }
    before = copy.deepcopy(args)
    result = checked_config("create_circuit_config", args)
    assert args == before and result["switch_views"] == args["switch_views"]
    assert '"schematic_alt"' in build_config_skill("create_circuit_config", args)


@pytest.mark.parametrize(
    "change",
    [
        {"switch_views": []},
        {"switch_views": ["spectre"] * 17},
        {"switch_views": ["spectre", "spectre"]},
        {"stop_views": []},
        {"stop_views": ["schematic"]},
        {"stop_views": ["missing"]},
        {"switch_views": ["spectre;exit()"]},
        {"library_list": ["work"]},
    ],
)
def test_unsupported_or_ambiguous_config_lists_rejected(change):
    with pytest.raises(CircuitArgumentError):
        checked_config("create_circuit_config", {**SPEC, **change})
