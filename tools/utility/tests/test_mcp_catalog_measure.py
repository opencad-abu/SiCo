"""Frozen measurement contract for the MCP catalog report."""

from mcp_catalog_measure import (
    compare,
    encoded,
    measurement,
    structural_schema,
    tool_bytes,
    wire_row,
)


def row(name, description="d", schema=None, annotations=None):
    return {
        "name": name,
        "description": description,
        "inputSchema": schema if schema is not None else {"type": "object"},
        "annotations": annotations if annotations is not None else {"readOnlyHint": True},
    }


def test_encoding_is_utf8_and_compact_is_sorted():
    assert encoded({"b": 1, "a": "库"}) == b'{"b": 1, "a": "\xe5\xba\x93"}'
    assert encoded({"b": 1, "a": "库"}, compact=True) == b'{"a":"\xe5\xba\x93","b":1}'


def test_wire_row_requires_all_four_fields():
    definition = {**row("t"), "title": "T"}
    assert set(wire_row(definition)) == {
        "name", "description", "inputSchema", "annotations"}
    for field in ("name", "description", "inputSchema", "annotations"):
        with_complete = row("t")
        with_complete.pop(field)
        try:
            wire_row(with_complete)
        except ValueError as error:
            assert field in str(error)
        else:
            raise AssertionError(f"missing {field} must be rejected")


def test_structural_schema_drops_annotations_at_any_depth():
    schema = {
        "type": "object",
        "description": "top",
        "properties": {
            "a": {"type": "string", "description": "leaf"},
            "b": {
                "type": "array",
                "title": "list",
                "items": {"type": "integer", "examples": [1]},
            },
        },
        "oneOf": [{"const": "x", "$comment": "why"}],
        "$defs": {"D": {"type": "object", "title": "D"}},
    }
    structural = structural_schema(schema)
    assert structural == {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "array", "items": {"type": "integer"}},
        },
        "oneOf": [{"const": "x"}],
        "$defs": {"D": {"type": "object"}},
    }


def test_tool_bytes_reports_schema_annotation_bytes():
    schema = {
        "type": "object", "description": "long explanation",
        "properties": {"a": {"type": "string"}},
    }
    measured = tool_bytes(row("t", schema=schema))
    assert measured["name"] == "t"
    assert measured["compact_bytes"] == len(encoded(row("t", schema=schema), compact=True))
    # The annotation share is the encoded schema minus its structure-only form.
    assert measured["annotation_bytes"] == (
        len(encoded(schema)) - len(encoded({
            "type": "object", "properties": {"a": {"type": "string"}}})))
    assert measured["schema_bytes"] - measured["annotation_bytes"] == len(encoded({
        "type": "object", "properties": {"a": {"type": "string"}}}))


def test_measurement_is_deterministic_and_sorted():
    rows = [row("b"), row("a"), row("c")]
    first, second = measurement(rows), measurement(list(rows))
    assert first == second
    assert [entry["name"] for entry in first["tools"]] == ["a", "b", "c"]
    assert first["count"] == 3
    assert first["array_compact_bytes"] == len(encoded([
        row("b"), row("a"), row("c")], compact=True))
    # Array bytes are the item sum plus separators and brackets.
    assert first["array_compact_bytes"] == first["sum_compact_bytes"] + 4
    assert first["largest"][0]["name"] in {"a", "b", "c"}


def test_compare_reports_added_removed_renamed_and_changed():
    old = [row("keep"), row("gone", description="gone-body"),
           row("moved", description="same"),
           row("edited", description="before")]
    new = [row("keep"), row("moved2", description="same"),
           row("edited", description="after"), row("fresh", description="fresh-body")]
    result = compare(
        measurement(old)["tools"], measurement(new)["tools"])
    assert result["added"] == ["fresh"]
    assert result["removed"] == ["gone"]
    assert result["renamed"] == [{"from": "moved", "to": "moved2"}]
    assert result["changed"] == ["edited"]
    assert result["unchanged_count"] == 1


def test_compare_leaves_ambiguous_duplicate_content_as_add_remove():
    old = [row("a", description="x"), row("b", description="x")]
    new = [row("c", description="x"), row("d", description="x")]
    result = compare(measurement(old)["tools"], measurement(new)["tools"])
    # Two removals and two additions share one content hash: not a unique
    # rename, so the diff refuses to guess and reports both sides.
    assert result["renamed"] == []
    assert result["added"] == ["c", "d"] and result["removed"] == ["a", "b"]
