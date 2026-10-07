"""Bounded truth tables for conditional CDF input rules, without executing callbacks."""

from itertools import product
from math import prod

from .constraints import condition


def references(value):
    if isinstance(value, dict):
        if set(value) == {"param"}:
            return {value["param"]}
        return set().union(*(references(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(references(v) for v in value))
    return set()


def rows(cdf):
    parameters = cdf["parameters"]
    expressions = [
        p[key] for p in parameters.values() for key in ("write_when", "required_when") if key in p
    ]
    selectors = sorted(references(expressions))
    domains = [parameters[name]["domain"] for name in selectors]
    if any(d["kind"] != "set" for d in domains) or prod(len(d["values"]) for d in domains) > 256:
        return [
            {
                "status": "incomplete",
                "reason": "Condition selectors need finite sets with at most 256 combinations",
                "selectors": selectors,
            }
        ], "incomplete"
    result = []
    for choices in product(*(d["values"] for d in domains)):
        values = dict(zip(selectors, choices))
        writable, explicit, inactive, unresolved = [], [], [], []
        for name, p in parameters.items():
            write = (
                condition(p["write_when"], parameters, values)
                if p["write"] == "conditional"
                else (None if p["write"] == "unknown" else p["write"] == "allow")
            )
            required = (
                condition(p["required_when"], parameters, values)
                if p["requirement"] == "conditional"
                else (None if p["requirement"] == "unknown" else p["requirement"] == "explicit")
            )
            if write is True:
                writable.append(name)
            elif write is False and p["write"] == "conditional":
                inactive.append(name)
            if required is True:
                explicit.append(name)
            if write is None or required is None:
                unresolved.append(name)
        result.append(
            {
                "id": "mode_" + str(len(result)),
                "selectors": values,
                "writable": writable,
                "explicit_required": explicit,
                "inactive_conditional_inputs": inactive,
                "unresolved": unresolved,
            }
        )
    return result, "incomplete" if any(r["unresolved"] for r in result) else "ok"
