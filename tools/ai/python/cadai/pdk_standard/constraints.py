"""Typed CDF domains and closed, three-valued parameter conditions."""

import math
from decimal import Decimal

from .jsonio import fail


def unknown(value):
    return isinstance(value, dict) and value.get("state") in {"unknown", "not_applicable"}


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value)


def typed(value, typ):
    if not isinstance(typ, str):
        return False
    return {"number": numeric(value), "integer": type(value) is int,
            "string": isinstance(value, str), "boolean": type(value) is bool}.get(typ, False)


def condition(rule, parameters, values=None, depth=1, budget=None):
    budget = [0] if budget is None else budget
    if not isinstance(rule, dict) or depth > 4:
        fail("Invalid condition tree")
    if set(rule) in ({"all"}, {"any"}):
        key = next(iter(rule))
        children = rule[key]
        if not isinstance(children, list) or not 1 <= len(children) <= 32:
            fail("Invalid condition operands")
        results = [condition(c, parameters, values, depth + 1, budget) for c in children]
        decisive = False if key == "all" else True
        return decisive if decisive in results else None if None in results else not decisive
    if set(rule) == {"not"}:
        result = condition(rule["not"], parameters, values, depth + 1, budget)
        return None if result is None else not result
    if set(rule) != {"op", "left", "right"} or rule["op"] not in {"eq", "ne", "lt", "le", "gt", "ge", "in"}:
        fail("Unsupported condition")
    budget[0] += 1
    if budget[0] > 32:
        fail("Condition exceeds 32 comparisons")
    operands, definitions, literals = [], [], []
    for value in (rule["left"], rule["right"]):
        if isinstance(value, dict):
            if set(value) != {"param"} or not isinstance(value["param"], str) or value["param"] not in parameters:
                fail("Unknown condition parameter")
            definitions.append(parameters[value["param"]])
            value = (values or {}).get(value["param"])
        else:
            literals.extend(value if isinstance(value, list) else [value])
        operands.append(value)
    if len(definitions) == 2 and any(definitions[0][k] != definitions[1][k] for k in ("type", "unit")):
        fail("Condition parameter types/units differ")
    typ = definitions[0]["type"] if definitions else None
    if typ and isinstance(typ, str) and any(not typed(v, typ) for v in literals):
        fail("Condition literal type differs from its parameter")
    if rule["op"] == "in":
        if not isinstance(rule["right"], list) or not rule["right"]:
            fail("in requires a nonempty literal array")
    elif isinstance(rule["left"], list) or isinstance(rule["right"], list):
        fail("Array only supported on the right of in")
    if values is None or any(v is None or unknown(v) for v in operands):
        return None
    left, right = operands
    compare = right[0] if rule["op"] == "in" else right
    if not ((numeric(left) and numeric(compare)) or type(left) is type(compare)):
        fail("Incompatible condition operands")
    if rule["op"] in {"lt", "le", "gt", "ge"} and (type(left) is bool or type(compare) is bool):
        fail("Boolean ordering is unsupported")
    try:
        return {"eq": lambda: left == right, "ne": lambda: left != right,
                "lt": lambda: left < right, "le": lambda: left <= right,
                "gt": lambda: left > right, "ge": lambda: left >= right,
                "in": lambda: left in right}[rule["op"]]()
    except TypeError:
        fail("Incompatible condition operands")


def domain(rule, typ, parameters):
    if not isinstance(rule, dict):
        fail("CDF domain must be an object")
    kind = rule.get("kind")
    fields = {"interval": {"kind", "min", "max", "min_closed", "max_closed"},
              "set": {"kind", "values"}, "conditional": {"kind", "cases", "otherwise"},
              "unknown": {"kind", "reason"}, "not_applicable": {"kind", "reason"},
              "unbounded": {"kind", "reason"}}
    if not isinstance(kind, str) or kind not in fields or set(rule) - fields[kind]:
        fail("Unsupported CDF domain")
    if kind == "interval":
        if typ not in {"number", "integer"} or not (set(rule) & {"min", "max"}):
            fail("Numeric interval requires a boundary")
        for key in ("min", "max"):
            if key in rule and not typed(rule[key], typ):
                fail("Invalid interval boundary")
        if "min" in rule and "max" in rule:
            if rule["min"] > rule["max"] or (rule["min"] == rule["max"] and
                    not (rule.get("min_closed", True) and rule.get("max_closed", True))):
                fail("Empty CDF interval")
        for key in ("min_closed", "max_closed"):
            if key in rule and type(rule[key]) is not bool:
                fail("Boundary closure must be boolean")
    elif kind == "set":
        rows = rule.get("values")
        if not isinstance(rows, list) or not rows or any(not typed(v, typ) for v in rows):
            fail("Invalid discrete CDF domain")
        if len(set(rows)) != len(rows):
            fail("Duplicate discrete values")
    elif kind == "conditional":
        if not isinstance(rule.get("cases"), list) or not rule["cases"]:
            fail("Conditional domain requires cases")
        for case in rule["cases"]:
            if not isinstance(case, dict) or set(case) - {"when", "domain", "grid"}:
                fail("Invalid domain case")
            condition(case.get("when"), parameters)
            if (case.get("domain") or {}).get("kind") == "conditional":
                fail("Nested conditional domains are unsupported")
            domain(case.get("domain"), typ, parameters)
            if "grid" in case:
                grid(case["grid"])
                if typ not in {"number", "integer"}:
                    fail("Case grid requires numeric parameter")
        if "otherwise" in rule:
            if not isinstance(rule["otherwise"], dict):
                fail("otherwise must be a domain")
            if rule["otherwise"].get("kind") == "conditional":
                fail("Nested conditional domains are unsupported")
            domain(rule["otherwise"], typ, parameters)
    elif not isinstance(rule.get("reason"), str) or not rule["reason"]:
        fail("Domain state requires a reason")


def grid(rule):
    if (not isinstance(rule, dict) or set(rule) != {"step", "origin"}
            or not numeric(rule["step"]) or rule["step"] <= 0 or not numeric(rule["origin"])):
        fail("Invalid hard grid")


def accepts(value, param, values=None):
    typ, rule, lattice = param["type"], param["domain"], param.get("grid")
    if not isinstance(typ, str) or not typed(value, typ):
        return False
    if rule["kind"] == "conditional":
        if values is None:
            return None
        matches = []
        for case in rule["cases"]:
            result = condition(case["when"], (values or {}).get("definitions", {}),
                               (values or {}).get("values", {}))
            if result is None:
                return None
            if result:
                matches.append(case)
        if len(matches) > 1:
            fail("Overlapping conditional domains")
        if matches:
            rule, lattice = matches[0]["domain"], matches[0].get("grid", lattice)
        else:
            rule = rule.get("otherwise", {"kind": "unknown"})
    kind = rule["kind"]
    if kind in {"unknown", "not_applicable"}:
        return None
    if kind == "set" and value not in rule["values"]:
        return False
    if kind == "interval":
        for key, sign in (("min", -1), ("max", 1)):
            if key in rule and (sign * value > sign * rule[key] or
                    value == rule[key] and not rule.get(key + "_closed", True)):
                return False
    if lattice and (Decimal(str(value)) - Decimal(str(lattice["origin"]))) % Decimal(str(lattice["step"])):
        return False
    return True


def feasible(param):
    """Reject domains with no point on the declared decimal lattice."""
    rule, lattice = param["domain"], param.get("grid")
    if rule["kind"] == "conditional":
        for case in rule["cases"]:
            feasible({**param, "domain": case["domain"], **({"grid": case["grid"]} if "grid" in case else {})})
        if "otherwise" in rule:
            feasible({**param, "domain": rule["otherwise"]})
        return
    if not lattice:
        return
    if rule["kind"] == "set" and not any(accepts(v, param) for v in rule["values"]):
        fail("Grid and discrete domain have no common value")
    if rule["kind"] == "interval" and "min" in rule and "max" in rule:
        from decimal import ROUND_CEILING, ROUND_FLOOR
        step, origin = Decimal(str(lattice["step"])), Decimal(str(lattice["origin"]))
        low = (Decimal(str(rule["min"])) - origin) / step
        high = (Decimal(str(rule["max"])) - origin) / step
        first, last = low.to_integral_value(rounding=ROUND_CEILING), high.to_integral_value(rounding=ROUND_FLOOR)
        if not rule.get("min_closed", True) and first == low:
            first += 1
        if not rule.get("max_closed", True) and last == high:
            last -= 1
        if first > last:
            fail("Grid and interval have no common value")
