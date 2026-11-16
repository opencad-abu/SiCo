"""Validate the layout-relevant parameter contract in a device layout file."""

from .jsonio import fail
from .layout_common import (
    PARAMETER_REQUIREMENTS,
    PARAMETER_RESOLUTIONS,
    PARAMETER_SEMANTICS,
    PARAMETER_TYPES,
    domain,
    enum,
    string,
    typed,
)
from .validate import fields


def _effect(value, kind, domain_rule, label, terminals):
    if not isinstance(value, dict) or not value:
        fail(label + " must be a non-empty mapping")
    if "values" in domain_rule:
        if set(value) != set(domain_rule["values"]):
            fail(label + " must cover every domain value")
    elif kind == "boolean" and set(value) != {False, True}:
        fail(label + " must cover boolean true and false")
    for effect in value.values():
        if not isinstance(effect, dict):
            fail(label + " effects must be objects")
        fields(effect, (), ("tap", "abut", "shares_source_drain", "must_connect", "note"))
        if "tap" in effect:
            enum(effect["tap"], frozenset({"none", "merged_with_source_drain", "separate"}),
                 label + " tap")
        if "abut" in effect:
            enum(effect["abut"], frozenset({"none", "same", "diff", "same_ext", "diff_ext"}),
                 label + " abut")
        if "shares_source_drain" in effect and type(effect["shares_source_drain"]) is not bool:
            fail(label + " shares_source_drain must be boolean")
        if "must_connect" in effect:
            names = effect["must_connect"]
            if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
                fail(label + " must_connect must be a string array")
            terminals.update(names)
        if "note" in effect:
            string(effect["note"], label + " note", allow_empty=True)


def _parameter(key, row, cdf_parameters, terminals):
    string(key, "layout parameter name")
    fields(
        row,
        ("type", "unit", "meaning", "requirement", "layout_relevant", "resolution"),
        ("cdf", "value", "formula", "formula_inputs", "domain", "default",
         "condition", "semantic", "layout_effect", "note"),
    )
    if sum(name in row for name in ("cdf", "value", "formula")) != 1:
        fail("layout parameter requires exactly one of cdf/value/formula")
    kind = enum(row["type"], PARAMETER_TYPES, "parameter type")
    if row["unit"] is not None:
        string(row["unit"], "layout parameter unit")
    string(row["meaning"], "layout parameter meaning")
    requirement = enum(row["requirement"], PARAMETER_REQUIREMENTS, "parameter requirement")
    resolution = enum(row["resolution"], PARAMETER_RESOLUTIONS, "parameter resolution")
    if type(row["layout_relevant"]) is not bool:
        fail("layout_relevant must be boolean")
    if requirement == "explicit" and resolution != "explicit":
        fail("explicit requirement must resolve explicitly")
    if requirement == "conditional":
        string(row.get("condition"), "conditional parameter condition")
    elif "condition" in row:
        fail("condition is only valid for a conditional requirement")
    if "cdf" in row:
        string(row["cdf"], "layout cdf parameter")
        if cdf_parameters is not None and row["cdf"] not in cdf_parameters:
            fail("layout parameter references unknown CDF parameter: " + row["cdf"])
    elif "value" in row:
        typed(row["value"], kind, "layout parameter value")
    else:
        string(row["formula"], "layout parameter formula")
        inputs = row.get("formula_inputs")
        if not isinstance(inputs, list) or not inputs or any(
                not isinstance(item, str) or not item for item in inputs):
            fail("derived parameter requires non-empty formula_inputs")
        if resolution != "derived":
            fail("formula is only valid for a derived parameter")
    if resolution == "default":
        if "default" not in row:
            fail("default resolution requires a default value")
        typed(row["default"], kind, "layout parameter default")
    elif "default" in row:
        fail("default is only valid for default resolution")
    if resolution == "derived" and "formula" not in row:
        fail("derived resolution requires a formula")
    if resolution != "derived" and "formula" in row:
        fail("formula is only valid for a derived parameter")
    if "domain" in row:
        domain(row["domain"], kind, "layout parameter domain")
    if kind == "enum" and not isinstance(row.get("domain", {}).get("values"), list):
        fail("enum parameter requires domain.values")
    if "semantic" in row:
        enum(row["semantic"], PARAMETER_SEMANTICS, "parameter semantic")
    if "note" in row:
        string(row["note"], "layout parameter note", allow_empty=True)


def _require_domain(value, name):
    rule = value[name].get("domain")
    if not isinstance(rule, dict) or "state" in rule:
        fail(name + " requires a concrete domain")
    if "values" in rule and not rule["values"]:
        fail(name + " requires non-empty domain.values")
    if not isinstance(rule.get("values"), list) and not set(rule) & {"min", "max"}:
        fail(name + " requires domain bounds or values")


def validate_parameters(value, cdf_parameters=None):
    """Validate parameters and return terminal names referenced by effects."""
    if not isinstance(value, dict):
        fail("layout parameters must be a mapping")
    terminals = set()
    for key, row in value.items():
        _parameter(key, row, cdf_parameters, terminals)
    for name, row in value.items():
        for input_name in row.get("formula_inputs", []):
            if input_name not in value:
                fail("parameter " + name + " references unknown formula input: " + input_name)
        if "layout_effect" in row:
            rule = {"values": ["false", "true"]} if row["type"] == "boolean" else None
            if rule is None:
                _require_domain(value, name)
                rule = value[name]["domain"]
            _effect(row["layout_effect"], row["type"], rule,
                    name + " layout_effect", terminals)
    if "w" in value:
        width = value["w"]
        if (width.get("semantic") != "finger_width" or width["type"] != "number"
                or width["unit"] != "m"):
            fail("w must be a finger width in metres")
        if "fingers" in value and "m" in value and "total_width" not in value:
            fail("w requires a derived total_width parameter")
    expected = (
        ("l", "channel_length", "m", "number"),
        ("fingers", "finger_count", None, None),
        ("m", "multiplier", None, None),
        ("model", "model_name", None, None),
    )
    for name, semantic, unit, kind in expected:
        if name not in value:
            continue
        row = value[name]
        if row.get("semantic") != semantic or row["unit"] != unit:
            fail(name + " has incorrect semantic or unit")
        if kind is not None and row["type"] != kind:
            fail(name + " has incorrect type")
    if "model" in value and value["model"]["type"] not in {"string", "enum"}:
        fail("model must be a string enumeration")
    if "fingers" in value and value["fingers"]["type"] not in {"integer", "number"}:
        fail("fingers must be numeric")
    if "m" in value and value["m"]["type"] not in {"integer", "number"}:
        fail("m must be numeric")
    if "total_width" in value:
        row = value["total_width"]
        if (row.get("semantic") != "total_width" or row["type"] != "number"
                or row["unit"] != "m" or row["resolution"] != "derived"
                or row["formula"] != "w * fingers * m"
                or row.get("formula_inputs") != ["w", "fingers", "m"]):
            fail("total_width must be the derived formula w * fingers * m in metres")
    for name in ("fingers", "m"):
        if name in value:
            _require_domain(value, name)
    if "model" in value:
        _require_domain(value, "model")
        if not value["model"]["domain"].get("values"):
            fail("model requires domain.values")
    for name in ("bodytie_type", "bodytie_typeR"):
        if name in value:
            row = value[name]
            if row["type"] not in {"string", "enum"} or row["unit"] is not None:
                fail(name + " must be a unitless string enumeration")
            _require_domain(value, name)
            if "layout_effect" not in row:
                fail(name + " requires layout_effect tap behaviour")
    for name in ("bulkConnectL", "bulkConnectR"):
        if name in value and value[name]["type"] != "boolean":
            fail(name + " must be boolean")
    for name in ("leftShape", "rightShape"):
        if name in value:
            row = value[name]
            if row["type"] not in {"string", "enum"} or row["unit"] is not None:
                fail(name + " must be a unitless string enumeration")
            _require_domain(value, name)
    for name in ("left_bulkFlush", "right_bulkFlush"):
        if name in value and value[name]["type"] != "boolean":
            fail(name + " must be boolean")
    return terminals


__all__ = ["validate_parameters"]
