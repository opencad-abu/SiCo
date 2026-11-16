"""The advertised summary must not replace the authoritative nested schema."""

from cadai.mcp_wire_schema import COMPACT_TOOLS, compact_schema
from cadai.mcp_help import mcp_help


def test_large_circuit_schema_summary_keeps_top_level_shape():
    schema = {
        "type": "object",
        "properties": {
            "spec": {"type": "object", "properties": {"x": {"type": "string"}}},
            "mode": {"type": "string", "enum": ["a", "b"]},
        },
        "required": ["spec"],
        "additionalProperties": False,
    }
    compact = compact_schema("prepare_circuit_creation", schema)
    assert compact["required"] == ["spec"]
    assert compact["properties"]["spec"] == {
        "type": "object", "additionalProperties": True}
    assert compact["properties"]["mode"]["enum"] == ["a", "b"]
    assert "x" not in compact["properties"]["spec"]


def test_small_tools_are_not_projected():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    assert compact_schema("mcp_help", schema) is schema
    assert "prepare_circuit_creation" in COMPACT_TOOLS


def test_mcp_help_still_serves_the_full_nested_schema():
    result = mcp_help({
        "action": "schema", "tool": "preview_circuit_spec",
        "schema_path": "/properties/spec",
    })
    assert result["schema"]["type"] == "object"
    assert result["schema"]["properties"]["instances"]
