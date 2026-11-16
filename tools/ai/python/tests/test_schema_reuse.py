"""Factored tool schemas expand back to the reviewed explicit contract."""

import json
from pathlib import Path

import pytest

from cadai import mcp_definitions
from cadai.schema_refs import expand
from cadai.waveform_schema import PAIR_RECIPE

FIXTURES = Path(__file__).with_name("fixtures")
GOLDEN = {
    "measure_waveform_pair": "waveform_pair_schema_golden.json",
    "evaluate_waveform_specs": "waveform_spec_schema_golden.json",
    "prepare_template_circuit": "template_circuit_schema_golden.json",
}


def tool_schema(name):
    return next(row["inputSchema"] for row in mcp_definitions.TOOLS if row["name"] == name)


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_factored_schema_expands_to_the_golden_contract(name):
    schema = tool_schema(name)
    encoded = json.dumps(schema, ensure_ascii=False)
    assert "$defs" in schema and '"$ref": "#/$defs/' in encoded
    golden = json.loads((FIXTURES / GOLDEN[name]).read_text())
    assert expand(schema) == golden


def test_waveform_pair_explicit_recipe_remains_the_authored_source():
    # Recipe-union consumers read the explicit recipe; the wire projection is
    # derived from it, never a second hand-written definition.
    assert '"$ref"' not in json.dumps(PAIR_RECIPE, ensure_ascii=False)


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_factored_schema_is_smaller_than_its_expansion(name):
    schema = tool_schema(name)
    assert len(json.dumps(schema, ensure_ascii=False)) < len(
        json.dumps(expand(schema), ensure_ascii=False))


def test_help_schema_fragment_is_self_contained():
    from cadai.mcp_help import mcp_help

    served = mcp_help({"action": "schema", "tool": "measure_waveform_pair",
                       "schema_path": "/properties/recipes/items"})
    assert served["complete"] and '"$ref"' not in json.dumps(served["schema"])
    assert served["schema"]["properties"]["operation"]["enum"] == [
        "delay", "gain_sample", "gain_rms"]
