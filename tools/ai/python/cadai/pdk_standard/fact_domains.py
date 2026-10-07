"""Reject incomplete bounds and conflicting finite selector domains before ingestion."""

from itertools import product
from math import prod

from .constraints import accepts, condition
from .jsonio import fail
from .policy_modes import references


def completeness(fact, parameter):
    if fact["field"] not in {"domain", "grid"}:
        if fact["basis"] != "definition":
            fail("Definitions require definition basis")
        return
    if fact["basis"] != "input_constraint" or fact.get("complete") is not True:
        fail("Effective constraints require complete input_constraint evidence")
    if fact["field"] == "grid":
        # An unknown interval must not hide a contradiction with a known hard grid.
        if (
            not isinstance(parameter["default"], dict)
            and accepts(parameter["default"], {**parameter, "domain": {"kind": "unbounded"}})
            is False
        ):
            fail("Observed default conflicts with hard grid")
        return
    state = fact.get("grid_status")
    if state not in {"none", "specified"}:
        fail("Unknown hard grid must keep domain unresolved")
    rule = fact["value"]
    parts = (
        [c["domain"] for c in rule.get("cases", [])]
        if rule.get("kind") == "conditional"
        else [rule]
    )
    if "otherwise" in rule:
        parts.append(rule["otherwise"])
    for item in parts:
        if item.get("kind") == "interval":
            missing = {"min", "max"} - set(item)
            if missing != set(fact.get("unbounded_sides", [])):
                fail("Missing bounds require explicit evidence of unbounded sides")
        if item.get("kind") in {"unknown", "not_applicable"}:
            fail("Unknown or inapplicable domains are unresolved evidence, not established inputs")
        if item.get("kind") == "unbounded" and set(fact.get("unbounded_sides", [])) != {
            "min",
            "max",
        }:
            fail("Unbounded domains require explicit evidence for both sides")
    has_grid = "grid" in parameter or any("grid" in c for c in rule.get("cases", []))
    if (state == "specified") != has_grid:
        fail("Hard grid evidence differs from the parameter/case grid")


def conditional_cases(parameters, name):
    p = parameters[name]
    rule = p["domain"]
    if rule["kind"] != "conditional":
        return
    selectors = sorted(references([c["when"] for c in rule["cases"]]))
    if name in selectors:
        fail("A conditional domain cannot select itself")
    domains = [parameters[n]["domain"] for n in selectors]
    if any(d["kind"] != "set" for d in domains) or prod(len(d["values"]) for d in domains) > 256:
        fail("Conditional fact requires finite selectors with at most 256 combinations")
    defaults = {n: r["default"] for n, r in parameters.items()}
    for values in product(*(d["values"] for d in domains)):
        selected = dict(zip(selectors, values))
        matches = [c for c in rule["cases"] if condition(c["when"], parameters, selected)]
        if len(matches) > 1:
            fail("Overlapping conditional fact cases: " + str(selected))
        if not matches and "otherwise" not in rule:
            fail("Conditional fact leaves a selector combination uncovered: " + str(selected))
        if isinstance(p["default"], dict):
            continue
        active = (
            condition(p["write_when"], parameters, {**defaults, **selected})
            if p["write"] == "conditional"
            else p["write"] == "allow"
        )
        if (
            active
            and accepts(
                p["default"], p, {"definitions": parameters, "values": {**defaults, **selected}}
            )
            is False
        ):
            fail("Observed default conflicts with conditional fact: " + str(selected))
