"""Consumer contract for circuit plans; no PDK discovery or executable expressions."""

from __future__ import annotations

import hashlib
import json
import math
import re

from .circuit_schema import string_schema

SPEC_VERSION = "cad.circuit.spec.v1"
BINDING_VERSION = "cad.circuit.bindings.v1"
PLAN_VERSION = "cad.circuit.plan.v1"
MAX_INPUT_BYTES = 196608


class CircuitSpecError(ValueError):
    pass


def obj(properties, required=None, **extra):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties if required is None else required),
        "additionalProperties": False,
        **extra,
    }


def array(items, maximum, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


def enum(*values):
    return {"type": "string", "enum": list(values)}


ID = string_schema(96, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
# Virtuoso hierarchical schematic cells are emitted as names such as
# ``inv@sheet001``.  Keep path separators and bus syntax excluded, while
# allowing the ``@`` hierarchy marker returned by inspect_schematic.
DD_NAME = string_schema(96, pattern=r"^[A-Za-z_][A-Za-z0-9_.@-]*$")
# Scalar names, including a single selected bus member. Bus bundles are not flattened.
NET_NAME = string_schema(96, pattern=r"^[A-Za-z_][A-Za-z0-9_]*(?:<[0-9]+>)?!?$")
TEXT = string_schema(512)
REF = string_schema(256)
VALUE = {"type": ["string", "number", "boolean"], "maxLength": 1024}
BOOL = {"type": "boolean"}
TARGET = obj({key: DD_NAME for key in ("library", "cell", "view")})


def mapping(values, keys=ID, maximum=64):
    return {
        "type": "object",
        "propertyNames": keys,
        "maxProperties": maximum,
        "additionalProperties": values,
    }


INSTANCE = obj(
    {
        "id": ID,
        "master": ID,
        "name": ID,
        "role": enum("device", "dut", "stimulus", "load", "auxiliary"),
        "parameters": mapping(VALUE),
        "connections": mapping(NET_NAME, NET_NAME),
        "unconnected": mapping(TEXT, NET_NAME),
    },
    ("id", "master", "role", "parameters", "connections", "unconnected"),
)
PORT = obj({"name": NET_NAME, "direction": enum("input", "output", "inputOutput"), "net": NET_NAME})
NET = obj({"name": NET_NAME, "scope": enum("local", "global", "ground")})
NOTE = obj({"text": TEXT, "instances": array(ID, 64, 1)})
SPEC = obj(
    {
        "schema": enum(SPEC_VERSION),
        "project_ref": REF,
        "binding_snapshot": REF,
        "kind": enum("circuit", "testbench"),
        "target": TARGET,
        "instances": array(INSTANCE, 64, 1),
        "nets": array(NET, 256, 1),
        "ports": array(PORT, 128),
        "notes": array(NOTE, 32),
        "global_net_authorization": string_schema(512, minLength=1),
    },
    ("schema", "project_ref", "binding_snapshot", "kind", "target", "instances", "nets", "ports"),
)
PARAMETER = obj(
    {
        "name": ID,
        "type": enum("string", "integer", "number", "boolean"),
        "editable": BOOL,
        "unit": TEXT,
        "choices": array(VALUE, 64, 1),
    },
    ("name", "type", "editable"),
)
TERMINAL = obj({"name": NET_NAME, "direction": enum("input", "output", "inputOutput")})
CALLBACK = obj({"status": enum("none", "required", "unknown"), "extension_ref": REF,
                "order": array(ID, 16, 1)}, ("status",))
MASTER = obj(
    {
        "id": ID,
        "target": TARGET,
        "library_path": string_schema(2048),
        "revision": REF,
        "kind": enum("device", "design"),
        "terminals": array(TERMINAL, 64),
        "terminals_complete": BOOL,
        "parameters": array(PARAMETER, 256),
        "parameters_complete": BOOL,
        "callbacks": CALLBACK,
        "classification": obj({"kind": ID, "attributes": mapping(VALUE, ID, 32),
                               "source_ref": REF, "revision": REF}),
        "pdk_binding": obj(
            {**{key: REF for key in ("adapter_ref", "snapshot_ref", "device_ref", "revision")},
             # D5 netlist terminal evidence collected at bind time (optional, additive).
             "netlist_terminal_map": {"type": "object", "maxProperties": 64, "propertyNames": ID,
                                      "additionalProperties": REF},
             "netlist_terminal_map_status": enum("confirmed", "matched_by_name", "unverified"),
             "model_deck_terminals": array(REF, 64)},
            ("adapter_ref", "snapshot_ref", "device_ref", "revision"),
        ),
    },
    ("id", "target", "library_path", "revision", "kind", "terminals", "terminals_complete",
     "parameters", "parameters_complete", "callbacks"),
)
BINDINGS = obj(
    {
        "schema": enum(BINDING_VERSION),
        "project_ref": REF,
        "snapshot_ref": REF,
        "source_ref": REF,
        "evidence_kind": enum("session_capture", "test_fixture"),
        "masters": array(MASTER, 64),
    }
)


def canonical(value):
    try:
        result = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        result.encode("utf-8")
        return result
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise CircuitSpecError("input must be finite, bounded JSON") from exc


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def validate(value, schema, path="arguments"):
    """Validate the bounded subset used here, including bool versus number distinction."""
    if "type" not in schema:
        # Composition branches constrain already typed objects (required/not).
        schema = {"type": "object", "additionalProperties": True, **schema}
    typ = schema.get("type")
    if isinstance(typ, list):
        for candidate in typ:
            try:
                validate(value, {**schema, "type": candidate}, path)
                return
            except CircuitSpecError:
                pass
        raise CircuitSpecError(path + ": expected finite string/number/boolean value")
    valid = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "null": value is None,
    }.get(typ, False)
    if not valid:
        raise CircuitSpecError(path + ": expected " + str(typ))
    if typ == "object":
        if len(value) > schema.get("maxProperties", 256):
            raise CircuitSpecError(path + ": too many fields")
        props = schema.get("properties", {})
        if set(schema.get("required", ())) - set(value):
            raise CircuitSpecError(
                path + ": missing " + ", ".join(sorted(set(schema["required"]) - set(value)))
            )
        for key, child in value.items():
            if not isinstance(key, str):
                raise CircuitSpecError(path + ": field names must be strings")
            if "propertyNames" in schema:
                validate(key, schema["propertyNames"], path + ".key")
            rule = props.get(key, schema.get("additionalProperties", False))
            if rule is False:
                raise CircuitSpecError(path + ": unsupported field " + key)
            if rule is not True:
                validate(child, rule, path + "." + key)
    elif typ == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema["maxItems"]:
            raise CircuitSpecError(
                f"{path}: array size {len(value)} outside supported bounds "
                f"{schema.get('minItems', 0)}..{schema['maxItems']}"
            )
        for index, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{index}]")
        if schema.get("uniqueItems") and len({canonical(item) for item in value}) != len(value):
            raise CircuitSpecError(path + ": duplicate array item")
    elif typ == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 256):
            raise CircuitSpecError(f"{path}: text length {len(value)} outside supported bounds "
                                   f"{schema.get('minLength', 0)}..{schema.get('maxLength', 256)}")
        if value != value.strip():
            raise CircuitSpecError(path + ": surrounding whitespace is not allowed")
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise CircuitSpecError(path + ": control characters are not allowed")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise CircuitSpecError(
                path + (": must be an absolute path" if schema["pattern"].startswith("^/")
                        else ": value does not match required pattern " + schema["pattern"])
            )
    elif typ in {"integer", "number"}:
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite or not schema.get("minimum", -1e100) <= value <= schema.get("maximum", 1e100):
            raise CircuitSpecError(
                f"{path}: numeric value outside finite supported bounds "
                f"{schema.get('minimum', -1e100)}..{schema.get('maximum', 1e100)}"
            )
    if "enum" in schema and value not in schema["enum"]:
        raise CircuitSpecError(path + ": unsupported value " + str(value))
    if "not" in schema:
        try:
            validate(value, schema["not"], path)
        except CircuitSpecError:
            pass
        else:
            fields = list(schema["not"].get("required", []))
            fields += [key for branch in schema["not"].get("anyOf", [])
                       for key in branch.get("required", []) if key in value]
            raise CircuitSpecError(path + ": conflicting fields " + ", ".join(fields))
    for keyword in ("oneOf", "anyOf"):
        if keyword not in schema:
            continue
        matches, errors = 0, []
        for branch in schema[keyword]:
            try:
                validate(value, branch, path)
                matches += 1
            except CircuitSpecError as exc:
                errors.append(str(exc))
        if not matches or (keyword == "oneOf" and matches != 1):
            raise CircuitSpecError(path + ": choose one input source; "
                                   + "; or ".join(errors or ["conflicting alternatives"]))


def unique(rows, key, path):
    indexed = {}
    for row in rows:
        if row[key] in indexed:
            raise CircuitSpecError(path + ": duplicate " + row[key])
        indexed[row[key]] = row
    return indexed
