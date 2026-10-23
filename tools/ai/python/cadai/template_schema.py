"""Versioned, bounded contracts for persistent circuit reference templates."""

from __future__ import annotations

import hashlib
import json
import math
import re

from .circuit_schema import string_schema, tool
from .circuit_spec_schema import MASTER
from .template_reuse_input_schema import reuse_input, validate_intent

SCHEMA = "cad.circuit.template.v2"
SCHEMA_V1 = "cad.circuit.template.v1"
SCHEMA_V3 = "cad.circuit.template.v3"
LEGACY_SCHEMAS = (SCHEMA_V1, SCHEMA)
SCHEMAS = (*LEGACY_SCHEMAS, SCHEMA_V3)
CAPTURE_SCHEMA = "cad.template.capture.v1"
CAPTURE_SCHEMA_V2 = "cad.template.capture.v2"
COORDINATE_SPACE = "source_dbu_integer_relative"
LEGACY_COORDINATE_SPACE = "source_user_units"
RULE_VERSION = "20260915.2"
PIN_POLICY = {"inputOutput": "upper_left", "input": "lower_left", "output": "right"}
MAX_CAPTURE_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_BYTES = 90000
ASSETS = ("schematic", "symbol", "layout")
REF_PATTERN = r"^(?:tpl_|ctpl_)[0-9a-f]{64}$"


class TemplateError(ValueError):
    pass


class TemplateUnavailable(RuntimeError):
    pass


def require_legacy_consumer(record):
    if record.get("schema_version") == SCHEMA_V3:
        raise TemplateUnavailable("legacy consumer cannot consume template v3 reuse contracts")


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate(value, schema, path="arguments"):
    """Small recursive validator; reject unknown fields, bool-as-int and NaN."""
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise TemplateError(path + " must be an object")
        props = schema.get("properties", {})
        if len(value) > schema.get("maxProperties", 512):
            raise TemplateError(path + " has too many fields")
        if set(schema.get("required", [])) - value.keys():
            raise TemplateError(path + " is missing required fields")
        out = {}
        for key, val in value.items():
            if not isinstance(key, str) or len(key) > 256:
                raise TemplateError(path + " has an invalid field name")
            rule = props.get(key, schema.get("additionalProperties", False))
            if rule is False:
                raise TemplateError(path + "." + key + " is unsupported")
            out[key] = validate(val, rule, path + "." + key) if isinstance(rule, dict) else val
        for key, rule in props.items():
            if key not in out and "default" in rule:
                out[key] = rule["default"]
        return out
    if kind == "array":
        if not isinstance(value, list) or not schema.get("minItems", 0) <= len(value) <= schema.get(
            "maxItems", 512
        ):
            raise TemplateError(path + " must be a bounded array")
        result = [validate(v, schema["items"], path + "[]") for v in value]
        if schema.get("uniqueItems") and len({canonical(v) for v in result}) != len(result):
            raise TemplateError(path + " contains duplicates")
        return result
    if kind == "string":
        if (
            not isinstance(value, str)
            or not schema.get("minLength", 1) <= len(value) <= schema.get("maxLength", 256)
            or value != value.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise TemplateError(path + " must be bounded text without control characters")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise TemplateError(path + " has invalid characters")
    elif kind in ("integer", "number"):
        types = (int,) if kind == "integer" else (int, float)
        if (
            type(value) not in types
            or not schema.get("minimum", -1e15) <= value <= schema.get("maximum", 1e15)
            or not math.isfinite(value)
        ):
            raise TemplateError(path + " must be a finite number within bounds")
    elif kind == "boolean" and type(value) is not bool:
        raise TemplateError(path + " must be boolean")
    if "enum" in schema and value not in schema["enum"]:
        raise TemplateError(path + " has an unsupported value")
    return value


NAME = string_schema(256, pattern=r"^[^\s/\\]+$")
REF = string_schema(69, pattern=REF_PATTERN)
COUNT = {"type": "integer", "minimum": 0, "maximum": 100000}
TARGET = {
    "template_ref": REF,
    "library": NAME,
    "cell": NAME,
    "reuse": reuse_input(NAME),
    "classifications": {"type": "object", "maxProperties": 1024,
                        "additionalProperties": MASTER["properties"]["classification"]},
    "capture_schema": {"type": "string", "enum": [CAPTURE_SCHEMA, CAPTURE_SCHEMA_V2],
                       "default": CAPTURE_SCHEMA_V2},
    "schematic_view": {**NAME, "default": "schematic"},
    "symbol_view": {**NAME, "default": "symbol"},
    "layout_view": {**NAME, "default": "layout"},
    "assets": {
        "type": "array",
        "items": {"type": "string", "enum": list(ASSETS)},
        "minItems": 1,
        "maxItems": 3,
        "uniqueItems": True,
        "default": ["schematic", "symbol", "layout"],
    },
}
TOOLS = [
    tool(
        "project_circuit_template",
        "Publish a sanitized standard L1/L2/L3 projection of ONE saved raw ctpl_ reference. "
        "Offline: no Virtuoso call. Recomputes identity and capabilities, preserves use decisions. "
        "L1 omits masters/geometry/parameters; L2 adds explicitly approved masters and geometry; "
        "L3 retains observed physical literals. Master publication requires an exact "
        "target+fingerprint allowlist in master_policy; unknown fingerprints are rejected. "
        "Output omitted stores the projection privately. An explicit output directory publishes "
        "only sanitized packages, separately from source/evidence catalogs. Retained evidence "
        "stays private. Public projections support inspection/matching; creation requires raw.",
        {
            "template_ref": string_schema(69, pattern=r"^ctpl_[0-9a-f]{64}$"),
            "level": {"type": "string", "enum": ["L1", "L2", "L3"]},
            "output": string_schema(4096),
            "master_policy": {
                "type": "object", "additionalProperties": False, "required": ["masters"],
                "properties": {"masters": {
                    "type": "array", "maxItems": 256, "items": {
                        "type": "object", "additionalProperties": False,
                        "required": ["target", "fingerprint"], "properties": {
                            "target": {"type": "object", "additionalProperties": False,
                                       "required": ["library", "cell", "view"],
                                       "properties": {k: NAME for k in
                                                      ("library", "cell", "view")}},
                            "fingerprint": string_schema(71, pattern=r"^sha256:[0-9a-f]{64}$"),
                        },
                    },
                }},
            },
        },
        ("template_ref", "level"),
        True,
    ),
    tool(
        "extract_circuit_templates",
        "Capture one explicit cell's requested schematic/symbol/layout views read-only and publish "
        "a persistent private template under the launch workspace. Optional classifications map "
        "source instance names to explicit kind/attributes/source_ref/revision evidence; "
        "unclassified devices stay unknown (PDK names do not infer type). New captures publish "
        "standard 0.1.0 packages. reuse={mode:whole,allow_rename:boolean} requests a qualified "
        "whole-circuit template with all devices and pins protected; reuse.mode=core takes an "
        "explicit declared partition of core instances, optional groups and boundary terminals. "
        "On reuse failure the reference capture is retained (ok=false, reuse.template_ref=null; "
        "see reuse.issues). No source save, binding update or simulation.",
        TARGET,
        (),
        True,
        help_text=(
            "The whole mode selects capture v2 and requires a schematic plus explicit "
            "classifications for every device. In core mode provide core_instances, "
            "optional_groups and boundary_terminals (endpoint, allowed_device_kinds, "
            "min/max_additional_connections) plus the allow_omit_optional_groups/"
            "allow_add_boundary_group booleans; use source names, keep core and groups a partition "
            "of all devices, and note counts cover all non-core endpoints including kept optional "
            "devices. No geometry or names grant deletion rights; group boundary pins and omitted "
            "nets/ports are derived and authoritatively revalidated. A qualified template still "
            "needs target binding and geometry checks; missing views or bindings are reported. "
            "Flattened layout devices are not reconstructed. Use sequential cell calls "
            "for capture batches and the CLI for offline indexing. Contract changes create a new "
            "immutable reference. Source, assets and classifications are pinned; do not combine "
            "template_ref with live source options. Live capture requires Virtuoso; saved "
            "completion requires the workspace retained capture."),
    ),
    tool(
        "query_circuit_templates",
        "Search private, project (SICO_CIRCUIT_TEMPLATES_DIR) and bundled templates, "
        "merging fixed references, preferring private/project/builtin. For "
        "creation/style selection omit tier and source-library filters so preferences are "
        "considered; choose the first suitable tier after checking topology, interface and asset "
        "gaps — a name match alone is not suitability. Tier filters origin; all configured copies "
        "stay visible and are conflict-checked. With a standard index, responses use "
        "items/revision/complete/"
        "next_cursor (pass it as cursor). Returns bounded summaries, coverage and fixed version "
        "references; does not contact Virtuoso.",
        {
            "data_format": {"type": "string", "enum": ["standard", "legacy"]},
            "cursor": string_schema(2048),
            "purpose": {"type": "string", "enum": ["reference", "reuse", "investigate"]},
            "capability": {"type": "string", "enum": [
                "topology-v1", "whole-reuse-v1", "core-reuse-v1", "placement-reference-v1",
                "routing-reference-v1", "symbol-reference-v1", "layout-reference-v1"]},
            "query": string_schema(512),
            "tier": {"type": "string", "enum": ["builtin", "project", "private"]},
            "library": NAME,
            "cell": NAME,
            "category": {"type": "string", "enum": ["digital", "analog", "reference"]},
            "device_kind": string_schema(128),
            "topology_fingerprint": string_schema(64, pattern=r"^[0-9a-f]{64}$"),
            "min_devices": COUNT,
            "max_devices": COUNT,
            "required_asset": {"type": "string", "enum": list(ASSETS)},
            "rule_version": string_schema(
                32, pattern=r"^[0-9]+\.(?:[0-9]+|[a-z][a-z0-9-]*\.v?[0-9]+)$"
            ),
            "detail_level": {"type": "string", "enum": ["L1", "L2", "L3"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "offset": {"type": "integer", "minimum": 0, "maximum": 100000, "default": 0},
        },
        help_text=(
            "purpose=investigate includes unknown/failed entries; data_format=legacy searches old "
            "SQLite catalogs explicitly."),
    ),
    tool(
        "get_circuit_template",
        "Get one immutable template. Topology is a direct-level connection graph; "
        "details are paged and preview returns local SVG/HTML artifacts. Gaps are explicit.",
        {
            "template_ref": REF,
            "cursor": string_schema(2048),
            "section": {
                "type": "string",
                "enum": [
                    "collection", "placement", "routing", "relation", "parameter", "reuse",
                    "qualification", "sources", "file",
                    "summary",
                    "topology",
                    "schematic",
                    "symbol",
                    "layout",
                    "relations",
                    "routing_style",
                    "parameters",
                    "reuse_contract",
                    "preview",
                ],
                "default": "summary",
            },
            "entity": {
                "type": "string",
                "enum": [
                    "connections", "anchors", "annotations", "nodes", "segments", "crossings",
                    "checks", "items", "assets", "boundaries", "optional_groups", "constraints",
                    "all",
                    "devices",
                    "nets",
                    "ports",
                    "instances",
                    "rows",
                    "columns",
                    "mirrored_pairs",
                    "alignment",
                    "groups",
                    "shapes",
                    "pins",
                    "bindings",
                    "properties",
                    "interface_differences",
                    "instance_properties",
                    "binding_groups",
                    "unmapped_devices",
                    "binding_gaps",
                    "multiplicity_properties",
                    "contexts",
                    "mosaics",
                    "gaps",
                    "ignored_graphics",
                    "masters",
                ],
                "default": "all",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
            "offset": {"type": "integer", "minimum": 0, "maximum": 100000, "default": 0},
        },
        ("template_ref",),
    ),
    tool(
        "match_circuit_template",
        "Match two stored direct-level topologies by typed device/terminal/net "
        "graph isomorphism; returns an explicit mapping. Numeric sizing is ignored; device class, "
        "terminal identity, port directions and global semantics are preserved, and port renaming "
        "must be explicit. match_mode=core consumes a v3 reuse contract and enumerates typed "
        "boundary embeddings. Explicit omitted_groups may select up to eight whole declared "
        "groups. Bounded search can be inconclusive; no result authorizes creation.",
        {
            "template_ref": REF,
            "target_ref": REF,
            "match_mode": {"type": "string", "enum": ["exact", "core"], "default": "exact"},
            "omitted_groups": {"type": "array", "items": NAME, "maxItems": 8,
                               "uniqueItems": True, "default": []},
            "port_map": {
                "type": "object",
                "additionalProperties": string_schema(),
                "maxProperties": 256,
                "default": {},
            },
        },
        ("template_ref", "target_ref"),
        help_text=(
            "Exact matching requires optional NetworkX 3.2.1; core matching returns "
            "needs_mapping for symmetry."),
    ),
]
TOOL_NAMES = frozenset(t["name"] for t in TOOLS)


def arguments(name, values):
    definition = next(t for t in TOOLS if t["name"] == name)
    result = validate(values, definition["inputSchema"])
    if name == "extract_circuit_templates":
        if "reuse" in result:
            validate_intent(result["reuse"])
        if "template_ref" in result:
            if set(values) - {"template_ref", "classifications", "reuse"} or "reuse" not in result:
                raise TemplateError(
                    "saved completion accepts only template_ref, classifications, reuse")
            result = {k: v for k, v in result.items()
                      if k in {"template_ref", "classifications", "reuse"}}
        elif not {"library", "cell"} <= result.keys():
            raise TemplateError("live extraction requires library and cell")
    if name == "extract_circuit_templates" and "reuse" in result and "template_ref" not in result:
        if "schematic" not in result["assets"]:
            raise TemplateError("reuse requires the schematic asset")
        result["assets"] = ["schematic"] + [a for a in result["assets"] if a != "schematic"]
        if "capture_schema" in values and result["capture_schema"] != CAPTURE_SCHEMA_V2:
            raise TemplateError("reuse requires capture v2; omit capture_schema or use v2")
        result["capture_schema"] = CAPTURE_SCHEMA_V2
    if name == "extract_circuit_templates" and "classifications" in result:
        from .circuit_spec_schema import CircuitSpecError
        from .circuit_spec_schema import validate as validate_classifications

        try:
            validate_classifications(result["classifications"], TARGET["classifications"])
        except CircuitSpecError as exc:
            raise TemplateError(str(exc)) from exc
    if name == "query_circuit_templates" and result.get("min_devices", 0) > result.get(
        "max_devices", 100000
    ):
        raise TemplateError("min_devices exceeds max_devices")
    return result


def capture_skill(values, output_path):
    args = arguments("extract_circuit_templates", values)
    if "template_ref" in args:
        raise TemplateError("saved completion does not call the live collector")
    fields = [args[k] for k in ("library", "cell", "schematic_view", "symbol_view", "layout_view")]
    fields.append(str(output_path))
    return (
        "aiTplCapture("
        + " ".join(json.dumps(v, ensure_ascii=False) for v in fields)
        + " list("
        + " ".join(json.dumps(v) for v in args["assets"])
        + ") " + json.dumps(args["capture_schema"]) + ")"
    )
