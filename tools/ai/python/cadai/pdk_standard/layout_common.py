"""Shared primitives for the SICO-PDK-DATA Appendix E layout validators."""

import math
import re

from .jsonio import fail
from .validate import envelope, fields, identifier

LAYER_KINDS = frozenset({"conductor", "cut", "device", "marker", "text", "blockage"})
LAYER_USAGE = frozenset({
    "drawing", "pin", "label", "text", "boundary", "blockage", "device",
    "marker", "dummy", "fill", "keepout", "recognition", "outline",
    "abstraction",
})
ORIENTATIONS = frozenset({"R0", "R90", "R180", "R270", "MX", "MY", "MXR90", "MYR90"})
DIRECTIONS = frozenset({"input", "output", "inputOutput", "passive", "unknown"})
CELL_USAGE = frozenset({"device", "dummy", "fill", "tap", "guard_ring", "marker", "other"})
PARAMETER_TYPES = frozenset({"number", "integer", "boolean", "string", "enum"})
PARAMETER_REQUIREMENTS = frozenset({"explicit", "optional", "conditional"})
PARAMETER_RESOLUTIONS = frozenset({"explicit", "default", "derived"})
PARAMETER_SEMANTICS = frozenset({
    "finger_width", "channel_length", "finger_count", "multiplier", "model_name",
    "total_width", "body_tie", "bulk_connect", "abutment_shape", "bulk_flush",
})
PIN_SOURCES = frozenset({"oa_pin_attribute", "text_label", "none"})
PIN_SHAPE_KINDS = frozenset({"rect", "path", "polygon", "mixed", "unknown"})
VARIANTS = frozenset({"parameterized", "macro"})
RUNTIME_ONLY = frozenset({
    "bbox", "outline", "pins", "access", "blockages", "geometry",
    "geometries", "sd_pitch", "gate_pitch", "tile", "pin_access",
})

PROFILE_BASE = "sico.pdk.layout.base.v1"
PROFILE_DEVICE = "sico.pdk.layout.device.v1"
PROFILE_FIXED_HIERARCHY = "sico.pdk.layout.fixed_hierarchy.v1"
PROFILE_DENSITY = "sico.pdk.layout.density.v1"
REQUIRED_PROFILES = frozenset({PROFILE_BASE, PROFILE_DEVICE})
OPTIONAL_PROFILES = frozenset({PROFILE_FIXED_HIERARCHY, PROFILE_DENSITY})
PROFILES = REQUIRED_PROFILES | OPTIONAL_PROFILES

DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def finite(value, label, *, positive=False, nonnegative=False):
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value):
        fail(label + " must be a finite number")
    if positive and value <= 0:
        fail(label + " must be positive")
    if nonnegative and value < 0:
        fail(label + " must be non-negative")
    return value


def positive_int(value, label):
    if type(value) is not int or isinstance(value, bool) or value <= 0:
        fail(label + " must be a positive integer")
    return value


def nonnegative_int(value, label):
    if type(value) is not int or isinstance(value, bool) or value < 0:
        fail(label + " must be a non-negative integer")
    return value


def pair(value, label, *, positive=False, nonnegative=False):
    if not isinstance(value, list) or len(value) != 2:
        fail(label + " must be a two-element array")
    for item in value:
        finite(item, label, positive=positive, nonnegative=nonnegative)
    return value


def string(value, label, *, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value):
        fail(label + " must be a non-empty string")
    return value


def envelope_fields(value, required, optional):
    fields(value, required, optional)
    envelope(value)
    if any(not isinstance(item, str) for item in value["depends_on"]):
        fail("depends_on must contain strings")
    for pointer, source in value.get("evidence", {}).items():
        if not isinstance(pointer, str) or not isinstance(source, str):
            fail("evidence must map JSON Pointers to source IDs")


def enum(value, allowed, label):
    if not isinstance(value, str) or (value not in allowed and not value.startswith("x_")):
        fail("Unsupported " + label + ": " + str(value))
    return value


def enum_list(value, allowed, label, *, nonempty=True):
    if not isinstance(value, list) or (nonempty and not value):
        fail(label + " must be a non-empty array")
    if any(not isinstance(item, str) for item in value):
        fail(label + " must contain strings")
    if len(value) != len(set(value)):
        fail(label + " must not contain duplicates")
    for item in value:
        enum(item, allowed, label)
    return value


def typed(value, kind, label):
    valid = {
        "number": type(value) in (int, float) and not isinstance(value, bool),
        "integer": type(value) is int and not isinstance(value, bool),
        "boolean": type(value) is bool,
        "string": isinstance(value, str),
        "enum": isinstance(value, str),
    }.get(kind, False)
    if not valid:
        fail(label + " must match parameter type " + str(kind))
    if kind == "number" and not math.isfinite(value):
        fail(label + " must be finite")
    return value


def domain(value, kind, label):
    if not isinstance(value, dict) or not value:
        fail(label + " must be a non-empty object")
    if set(value) == {"state", "reason"}:
        if value["state"] not in {"unknown", "not_applicable"}:
            fail("Unsupported " + label + " state")
        string(value["reason"], label + " reason")
        return value
    if "values" in value:
        fields(value, ("values",))
        rows = value["values"]
        if not isinstance(rows, list) or not rows:
            fail(label + " values must be a non-empty array")
        for item in rows:
            typed(item, kind, label + " value")
        if len({repr(item) for item in rows}) != len(rows):
            fail(label + " values must be unique")
        return value
    fields(value, (), ("min", "max"))
    if not set(value) & {"min", "max"}:
        fail(label + " requires min, max, or values")
    for key in ("min", "max"):
        if key in value:
            typed(value[key], kind, label + " " + key)
    if "min" in value and "max" in value and value["min"] > value["max"]:
        fail(label + " min exceeds max")
    return value


def digest(value, label):
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        fail(label + " must be sha256:<64 lowercase hex>")
    return value


def lpp(value, label):
    text = string(value, label)
    name, separator, purpose = text.partition("/")
    if not separator or not name or not purpose or "/" in purpose:
        fail(label + " must be <layer>/<purpose>")
    return text


def layer_lpp_index(layers):
    """Map every declared OA ``name/purpose`` to its stable layer ID."""
    result = {}
    if not isinstance(layers, dict):
        return result
    for key, row in layers.items():
        if key == "$parts" or not isinstance(row, dict):
            continue
        if isinstance(row.get("name"), str) and isinstance(row.get("purpose"), str):
            result[row["name"] + "/" + row["purpose"]] = key
    return result


def process_layer_id(process, value, label):
    text = lpp(value, label)
    if process is not None:
        key = layer_lpp_index(process.get("layers")).get(text)
        if key is None:
            fail(label + " references unknown layer/purpose: " + text)
        return key
    return None


def routing_order_index(process):
    if process is None:
        return {}
    routing = process.get("routing")
    order = routing.get("layer_order") if isinstance(routing, dict) else None
    if not isinstance(order, list):
        return {}
    return {key: index for index, key in enumerate(order)}


def pin_group_members(process):
    if process is None:
        return set()
    groups = process.get("layer_groups")
    if not isinstance(groups, dict):
        return set()
    pin = groups.get("pin")
    members = pin.get("members") if isinstance(pin, dict) else None
    return set(members) if isinstance(members, list) else set()


def identifier_key(value, label):
    identifier(value)
    return value
