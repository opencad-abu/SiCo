"""One authoritative expansion for local $defs references in tool schemas."""

import pytest

from cadai.circuit_spec_schema import CircuitSpecError, obj, validate
from cadai.schema_refs import SchemaRefError, auto_factor, expand, factor

QUANTITY = obj({"value": {"type": "number"}, "unit": {"type": "string", "maxLength": 32}})
FACTORED = {
    "$defs": {"quantity": QUANTITY},
    "type": "object",
    "properties": {
        "start": {"$ref": "#/$defs/quantity"},
        "stop": {"$ref": "#/$defs/quantity"},
        "direction": {"type": "string", "enum": ["rising", "falling"]},
    },
    "required": ["start", "stop"],
    "additionalProperties": False,
}
EXPLICIT_ROOT = {
    "type": "object",
    "properties": {
        "start": QUANTITY,
        "stop": QUANTITY,
        "direction": {"type": "string", "enum": ["rising", "falling"]},
    },
    "required": ["start", "stop"],
    "additionalProperties": False,
}
SAMPLES = [
    {"start": {"value": 1.0, "unit": "V"}, "stop": {"value": 2.0, "unit": "V"}},
    {"start": {"value": 1.0, "unit": "V"}, "stop": {"value": 2.0, "unit": "V"},
     "direction": "rising"},
    {"start": {"value": 1.0, "unit": "V"}},
    {"stop": {"value": 2.0, "unit": "V"}},
    {"start": {"value": "one", "unit": "V"}, "stop": {"value": 2.0, "unit": "V"}},
    {"start": {"value": 1.0}, "stop": {"value": 2.0, "unit": "V"}},
    {"start": {"value": 1.0, "unit": "V"}, "stop": {"value": 2.0, "unit": "V"},
     "direction": "either"},
    {"start": {"value": 1.0, "unit": "V"}, "stop": {"value": 2.0, "unit": "V"},
     "extra": 1},
]


def outcome(schema, sample):
    try:
        validate(sample, schema)
        return None
    except CircuitSpecError as exc:
        return str(exc)


def test_expansion_reproduces_the_explicit_contract():
    assert expand(FACTORED) == EXPLICIT_ROOT
    assert "$defs" not in expand(FACTORED)


def test_ref_and_explicit_validate_identically():
    results = [(outcome(FACTORED, sample), outcome(EXPLICIT_ROOT, sample))
               for sample in SAMPLES]
    assert all(factored == explicit for factored, explicit in results)
    assert results[0] == (None, None) and results[1] == (None, None)
    assert all(row[0] is not None for row in results[2:])


def test_fragment_expansion_uses_the_supplied_root():
    fragment = {"$ref": "#/$defs/quantity"}
    assert expand(fragment, root=FACTORED) == QUANTITY


def test_refs_are_independent_copies():
    first = expand({"$ref": "#/$defs/quantity"}, root=FACTORED)
    first["properties"]["unit"]["maxLength"] = 1
    assert FACTORED["$defs"]["quantity"]["properties"]["unit"]["maxLength"] == 32


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "#/$defs/missing", "$defs": {}},
        {"$ref": "#/definitions/other"},
        {"$ref": 7},
        {"$ref": "#/$defs/a", "type": "object", "$defs": {"a": {}}},
    ],
)
def test_invalid_references_are_errors(schema):
    with pytest.raises(SchemaRefError):
        expand(schema)


def test_circular_reference_is_an_error():
    schema = {
        "$defs": {
            "a": {"type": "object", "properties": {"next": {"$ref": "#/$defs/b"}}},
            "b": {"type": "object", "properties": {"next": {"$ref": "#/$defs/a"}}},
        },
        "type": "object",
        "properties": {"head": {"$ref": "#/$defs/a"}},
    }
    with pytest.raises(SchemaRefError, match="circular"):
        expand(schema)


def test_validate_reports_unresolvable_references():
    with pytest.raises(CircuitSpecError, match="unknown \\$defs"):
        validate(SAMPLES[0], {"type": "object", "properties": {"start": {"$ref": "#/$defs/q"}}})


def test_references_inside_composition_and_arrays():
    schema = {
        "$defs": {"quantity": QUANTITY},
        "type": "object",
        "properties": {
            "window": obj({"start": {"$ref": "#/$defs/quantity"},
                           "stop": {"$ref": "#/$defs/quantity"}}),
            "events": {"type": "array", "minItems": 1, "maxItems": 3,
                       "items": {"$ref": "#/$defs/quantity"}},
        },
        "required": ["window"],
        "additionalProperties": False,
    }
    validate({"window": {"start": {"value": 0.0, "unit": "s"},
                         "stop": {"value": 1.0, "unit": "s"}},
              "events": [{"value": 1.0, "unit": "V"}]}, schema)
    with pytest.raises(CircuitSpecError):
        validate({"window": {"start": {"value": 0.0}, "stop": {"value": 1.0, "unit": "s"}}},
                 schema)
    with pytest.raises(CircuitSpecError):
        validate({"window": {"start": {"value": 0.0, "unit": "s"},
                             "stop": {"value": 1.0, "unit": "s"}},
                  "events": []}, schema)


def test_factor_replaces_repeated_structures_and_round_trips():
    defs = {
        "quantity": QUANTITY,
        "window": {
            "type": "object",
            "properties": {"start": {"$ref": "#/$defs/quantity"},
                           "stop": {"$ref": "#/$defs/quantity"}},
            "required": ["start", "stop"],
            "additionalProperties": False,
        },
    }
    window = expand({"$ref": "#/$defs/window"}, root={"$defs": defs})
    schema = {
        "type": "object",
        "properties": {
            "first": window,
            "second": window,
            "at": QUANTITY,
            "extra": {"type": "integer", "minimum": 1, "maximum": 5},
        },
        "required": ["first"],
        "additionalProperties": False,
    }
    factored, used = factor(schema, defs)
    assert set(used) == {"quantity", "window"}
    assert factored["properties"]["first"] == {"$ref": "#/$defs/window"}
    assert factored["properties"]["second"] == {"$ref": "#/$defs/window"}
    assert factored["properties"]["extra"] == {"type": "integer", "minimum": 1, "maximum": 5}
    assert expand(factored, root={"$defs": used}) == schema


def test_factor_keeps_small_or_unused_definitions_out():
    small = {"type": "string", "maxLength": 8}
    schema = {"type": "object", "properties": {"one": small, "two": small,
                                               "free": {"type": "string"}}}
    factored, used = factor(schema, {"small": small, "free": {"type": "string"}})
    assert used == {}
    assert factored == schema


def test_factor_prefers_the_largest_matching_target():
    inner = {"type": "string", "maxLength": 200, "minLength": 1,
             "pattern": "^[A-Za-z_][A-Za-z0-9_]*$"}
    outer = {"type": "object", "properties": {"name": inner, "again": inner},
             "required": ["name"], "additionalProperties": False}
    schema = {"type": "object", "properties": {"left": outer, "right": outer}}
    factored, used = factor(schema, {"inner": inner, "outer": outer})
    assert set(used) == {"outer"}
    assert factored["properties"]["left"] == {"$ref": "#/$defs/outer"}
    assert expand(factored, root={"$defs": used}) == schema


def test_factor_rejects_definitions_referencing_unknown_names():
    broken = {"$ref": "#/$defs/ghost"}
    schema = {"type": "object", "properties": {"a": broken, "b": broken}}
    with pytest.raises(SchemaRefError, match="unknown"):
        factor(schema, {"dangling": broken})


def test_auto_factor_round_trips_repeated_subtrees():
    schema = {
        "type": "object",
        "properties": {
            "start": QUANTITY,
            "stop": QUANTITY,
            "other": {"type": "string"},
        },
        "required": ["start", "stop"],
        "additionalProperties": False,
    }
    factored, used = auto_factor(schema)
    assert set(used) == {"d000"}
    assert factored["properties"]["start"] == {"$ref": "#/$defs/d000"}
    assert factored["properties"]["stop"] == {"$ref": "#/$defs/d000"}
    assert expand(factored) == schema


def test_auto_factor_leaves_authored_defs_and_unrepeated_schemas_alone():
    factored, used = auto_factor(FACTORED)
    assert expand(factored) == expand(FACTORED)
    assert set(used) == {"quantity"}
    plain = {"type": "object", "properties": {"one": {"type": "string"}}}
    assert auto_factor(plain) == (plain, {})


def test_auto_factor_keeps_authored_defs_and_adds_a_repeated_one():
    schema = {
        "$defs": {"quantity": QUANTITY},
        "type": "object",
        "properties": {
            "start": {"$ref": "#/$defs/quantity"},
            "stop": {"$ref": "#/$defs/quantity"},
            "left": {"type": "string", "maxLength": 200, "minLength": 1,
                     "pattern": "^[A-Za-z_][A-Za-z0-9_]*$"},
            "right": {"type": "string", "maxLength": 200, "minLength": 1,
                      "pattern": "^[A-Za-z_][A-Za-z0-9_]*$"},
            "top": {"type": "string", "maxLength": 200, "minLength": 1,
                    "pattern": "^[A-Za-z_][A-Za-z0-9_]*$"},
        },
    }
    factored, used = auto_factor(schema)
    assert set(used) >= {"quantity"}
    assert factored["properties"]["start"] == {"$ref": "#/$defs/quantity"}
    assert (factored["properties"]["left"] == factored["properties"]["right"]
            == factored["properties"]["top"])
    assert factored["properties"]["left"].get("$ref", "").startswith("#/$defs/d")
    assert expand(factored) == expand(schema)
