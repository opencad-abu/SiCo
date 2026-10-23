"""Consumer contract for circuit plans; no PDK discovery or executable expressions."""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import re

from .circuit_schema import string_schema
from .schema_refs import SchemaRefError, expand

SPEC_VERSION = "cad.circuit.spec.v1"
BINDING_VERSION = "cad.circuit.bindings.v1"
PLAN_VERSION = "cad.circuit.plan.v1"
MAX_INPUT_BYTES = 196608


class CircuitSpecError(ValueError):
    pass


class Problems:
    """Collect independent rule violations so one reply can list them together.

    Checks that can continue past a failed item record the problem and skip only
    that item's dependent work; prerequisites still fail immediately through
    ``fatal``.  The raised message keeps each original rule text verbatim, so
    callers and tests can match a single rule inside the combined report.
    """

    def __init__(self, subject, limit=8):
        self.subject, self.limit, self.rows, self.omitted = subject, limit, [], 0

    def add(self, rule, detail=""):
        row = rule + (" (" + detail + ")" if detail else "")
        if len(self.rows) < self.limit:
            self.rows.append(row)
        else:
            self.omitted += 1

    def fatal(self, message):
        raise CircuitSpecError(message)

    def raise_if_any(self):
        if not self.rows:
            return
        total = len(self.rows) + self.omitted
        listing = "; ".join(f"[{index}] {row}" for index, row in enumerate(self.rows, 1))
        omitted = f"; {self.omitted} more not listed" if self.omitted else ""
        raise CircuitSpecError(
            f"{self.subject} has {total} problem(s); fix them together: {listing}{omitted}")


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


def ref(name):
    """Reference one local ``$defs`` entry declared by the same tool schema."""
    return {"$ref": "#/$defs/" + name}


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


def _value_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _shown(value, limit=80):
    if isinstance(value, str):
        return repr(value if len(value) <= limit else value[:limit - 3] + "...")
    if isinstance(value, (int, float, bool)) or value is None:
        return repr(value)
    return _value_type(value)


def _field_suggestion(value, properties):
    candidates = [name for name in properties if isinstance(name, str)]
    close = difflib.get_close_matches(value, candidates, n=1, cutoff=0.55)
    return "; did you mean " + repr(close[0]) if close else ""


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


def validate(value, schema, path="arguments", *, _expanded=False):
    """Validate the bounded subset used here, including bool versus number distinction.

    Local ``$defs`` references are expanded once at the entry point through the
    shared ``schema_refs`` expansion, so a factored schema constrains exactly
    like its explicit form.  Recursive calls never carry references.
    """
    if not _expanded:
        try:
            schema = expand(schema)
        except SchemaRefError as exc:
            raise CircuitSpecError("schema: " + str(exc)) from exc
        _expanded = True
    if "type" not in schema and "enum" not in schema:
        # Composition branches constrain already typed objects (required/not).
        # A schema that only carries an enum is value-agnostic and must not be
        # coerced into an object (legacy Maestro output branches use this).
        schema = {"type": "object", "additionalProperties": True, **schema}
    typ = schema.get("type")
    if isinstance(typ, list):
        for candidate in typ:
            try:
                validate(value, {**schema, "type": candidate}, path, _expanded=True)
                return
            except CircuitSpecError:
                pass
        raise CircuitSpecError(
            path + ": expected one of " + ", ".join(map(str, typ))
            + "; got " + _value_type(value)
        )
    if typ is not None:
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
            raise CircuitSpecError(
                path + ": expected " + str(typ) + "; got " + _value_type(value)
            )
    if typ == "object":
        if len(value) > schema.get("maxProperties", 256):
            raise CircuitSpecError(
                f"{path}: too many fields ({len(value)} > "
                f"{schema.get('maxProperties', 256)})"
            )
        if len(value) < schema.get("minProperties", 0):
            raise CircuitSpecError(
                f"{path}: too few fields ({len(value)} < "
                f"{schema.get('minProperties', 0)})"
            )
        props = schema.get("properties", {})
        missing = sorted(set(schema.get("required", ())) - set(value))
        unsupported = []
        for key in value:
            rule = props.get(key, schema.get("additionalProperties", False))
            if rule is False:
                unsupported.append(key)
        if missing or unsupported:
            parts = []
            if missing:
                parts.append("missing " + ", ".join(missing))
            if unsupported:
                parts.append(
                    "unsupported field(s) " + ", ".join(map(str, sorted(unsupported)))
                )
                candidates = [name for name in props if isinstance(name, str)]
                hints = []
                for key in sorted(unsupported):
                    if isinstance(key, str):
                        hints.extend(difflib.get_close_matches(
                            key, candidates, n=1, cutoff=0.55
                        ))
                if hints:
                    parts.append("did you mean " + ", ".join(dict.fromkeys(hints)))
            allowed = [name for name in props if isinstance(name, str)]
            if unsupported:
                if allowed:
                    shown = allowed[:20]
                    parts.append("allowed fields: " + ", ".join(shown)
                                 + (", ..." if len(allowed) > len(shown) else ""))
                else:
                    parts.append("this tool accepts no fields")
            raise CircuitSpecError(path + ": " + "; ".join(parts))
        for key, child in value.items():
            if not isinstance(key, str):
                raise CircuitSpecError(path + ": field names must be strings")
            if "propertyNames" in schema:
                validate(key, schema["propertyNames"], path + ".key", _expanded=True)
            rule = props.get(key, schema.get("additionalProperties", False))
            if rule is False:
                raise CircuitSpecError(
                    path + ": unsupported field " + key
                    + _field_suggestion(key, props)
                )
            if rule is not True:
                validate(child, rule, path + "." + key, _expanded=True)
    elif typ == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema["maxItems"]:
            raise CircuitSpecError(
                f"{path}: array size {len(value)} outside supported bounds "
                f"{schema.get('minItems', 0)}..{schema['maxItems']}"
            )
        for index, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{index}]", _expanded=True)
        if schema.get("uniqueItems") and len({canonical(item) for item in value}) != len(value):
            raise CircuitSpecError(path + ": duplicate array item")
    elif typ == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 256):
            bounds = (f"{schema.get('minLength', 0)}.."
                      f"{schema.get('maxLength', 256)}")
            raise CircuitSpecError(
                f"{path}: text length {len(value)} outside supported bounds {bounds}; "
                f"got {_shown(value)}"
            )
        # These top-level fields carry executable or hardware source, where
        # newlines are meaningful.  Single-line query/name/path fields retain
        # the control-character checks below.
        multiline = path in {"arguments.code", "arguments.source"}
        if not multiline and value != value.strip():
            raise CircuitSpecError(
                path + ": surrounding whitespace is not allowed; got " + _shown(value)
            )
        if not multiline and any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise CircuitSpecError(
                path + ": control characters are not allowed; provide plain single-line text"
            )
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            shown = value if len(value) <= 80 else value[:77] + "..."
            description = schema.get("description")
            expected = (
                " (" + description + ")"
                if isinstance(description, str) and description and len(description) <= 160
                else ""
            )
            raise CircuitSpecError(
                path + (f": must be an absolute path (got {shown!r})"
                        if schema["pattern"].startswith("^/")
                        else ": value does not match required pattern "
                        + schema["pattern"] + f" (got {shown!r})" + expected)
            )
    elif typ in {"integer", "number"}:
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite or not schema.get("minimum", -1e100) <= value <= schema.get("maximum", 1e100):
            raise CircuitSpecError(
                f"{path}: numeric value outside finite supported bounds "
                f"{schema.get('minimum', -1e100)}..{schema.get('maximum', 1e100)}; "
                f"got {_shown(value)}"
            )
    if "enum" in schema and value not in schema["enum"]:
        allowed = [item for item in schema["enum"] if isinstance(item, str)]
        hint = ""
        if isinstance(value, str):
            close = difflib.get_close_matches(value, allowed, n=1, cutoff=0.55)
            if close:
                hint = "; did you mean " + repr(close[0])
        raise CircuitSpecError(
            path + ": unsupported value " + str(value)
            + f" (got {_shown(value)})"
            + hint + "; allowed values: " + ", ".join(map(str, schema["enum"]))
        )
    if "not" in schema:
        try:
            validate(value, schema["not"], path, _expanded=True)
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
                validate(value, branch, path, _expanded=True)
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
