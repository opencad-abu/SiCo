"""Explicit decimal unit scaling for facts; no inferred dimensions or offsets."""

from copy import deepcopy
from decimal import Decimal

from .constraints import numeric
from .jsonio import fail

BASE = {
    u: (u, Decimal(1))
    for u in ("1", "m", "m2", "V", "A", "Ohm", "Ohm/sq", "F", "H", "Hz", "s", "K", "degC", "S")
}
PREFIX = {"p": -12, "n": -9, "u": -6, "µ": -6, "m": -3, "k": 3, "M": 6, "G": 9}


def unit(name):
    if not isinstance(name, str):
        fail("Explicit fact unit must be a string", "pdk_fact_unit_conflict")
    if name in BASE:
        return BASE[name]
    for prefix, exponent in PREFIX.items():
        suffix = name[len(prefix) :] if isinstance(name, str) and name.startswith(prefix) else None
        if suffix in {"m", "V", "A", "Ohm", "F", "H", "Hz", "s", "S"}:
            return suffix, Decimal(10) ** exponent
        if suffix == "m2":
            return "m2", Decimal(10) ** (2 * exponent)
    fail("Unsupported explicit fact unit: " + str(name), "pdk_fact_unit_conflict")


def convert(value, source, target):
    left, a = unit(source)
    right, b = unit(target)
    if left != right:
        fail("Fact dimensions differ: " + source + " -> " + target, "pdk_fact_unit_conflict")
    if not numeric(value):
        fail("Unit conversion requires a finite JSON number")
    number = Decimal(str(value)) * a / b
    result = int(number) if number == number.to_integral_value() else float(number)
    if not numeric(result) or (number != 0 and result == 0):
        fail("Unit conversion overflow")
    return result


def scale(field, value, source, target):
    if unit(source)[0] != unit(target)[0]:
        fail("Fact dimensions differ: " + source + " -> " + target, "pdk_fact_unit_conflict")
    if field == "default":
        if isinstance(value, (str, bool)) or isinstance(value, dict):
            if source != target:
                fail("Cannot scale a string, boolean or unknown default")
            return deepcopy(value)
        return convert(value, source, target)
    if field == "grid":
        if not isinstance(value, dict) or set(value) != {"origin", "step"}:
            fail("Grid requires origin and step")
        return {k: convert(v, source, target) for k, v in value.items()}
    rule = deepcopy(value)
    if not isinstance(rule, dict):
        fail("Domain fact requires an object")
    if rule.get("kind") == "interval":
        for key in ("min", "max"):
            if key in rule:
                rule[key] = convert(rule[key], source, target)
    elif rule.get("kind") == "set":
        rule["values"] = [convert(v, source, target) if numeric(v) else v for v in rule["values"]]
        if any(not numeric(v) for v in rule["values"]) and source != target:
            fail("Cannot scale string or boolean domain")
    elif rule.get("kind") == "conditional":
        for case in rule["cases"]:
            case["domain"] = scale("domain", case["domain"], source, target)
            if "grid" in case:
                case["grid"] = scale("grid", case["grid"], source, target)
        if "otherwise" in rule:
            rule["otherwise"] = scale("domain", rule["otherwise"], source, target)
        # Conditions refer to other parameters in their declared units, not this unit.
    return rule
