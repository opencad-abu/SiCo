"""Validate editable mode inputs without inferring legal ranges from samples."""

from .constraints import condition, typed, unknown
from .gate import value
from .interface import collection_parameters
from .jsonio import TARGET, encode, fail
from .policy_modes import rows as mode_rows


def phase(cdf, assignments):
    parameters = cdf["parameters"]
    if not assignments or set(assignments) - set(parameters):
        fail("Case requires known literal CDF inputs")
    resolved = {n: p["default"] for n, p in parameters.items() if not unknown(p["default"])}
    for name, literal in assignments.items():
        p = parameters[name]
        if not isinstance(p["type"], str):
            fail("Case input type is unknown: " + name)
        normalized = value(literal, p)
        if not typed(normalized, p["type"]):
            fail("Case value type differs: " + name)
        resolved[name] = normalized
    active = []
    for name, p in parameters.items():
        writable = p["write"] == "allow" or (
            p["write"] == "conditional" and condition(p["write_when"], parameters, resolved) is True
        )
        if name in assignments and not writable:
            fail("Case cannot write denied or inactive input: " + name, "pdk_policy_mismatch")
        if writable:
            active.append(name)
            required = p["requirement"] == "explicit" or (
                p["requirement"] == "conditional"
                and condition(p["required_when"], parameters, resolved) is True
            )
            if required and name not in assignments:
                fail("Case must explicitly specify active required input: " + name)
    modes, _ = mode_rows(cdf)
    selected = [
        r
        for r in modes
        if isinstance(r.get("selectors"), dict)
        and all(resolved.get(k) == v for k, v in r["selectors"].items())
    ]
    if len(selected) != 1:
        fail("Case mode is unresolved or unsupported")
    return {
        "mode": selected[0]["id"],
        "selectors": selected[0]["selectors"],
        "active": sorted(active),
        "values": {n: resolved[n] for n in active if n in resolved},
    }


def prepare(cdf, order, cases):
    inputs, _ = collection_parameters(cdf)
    if set(order) != inputs or len(order) != len(inputs):
        fail("Order must list every confirmed core input exactly once")
    seen = set()
    result = {}
    for position, case in enumerate(cases):
        if case["id"] in seen:
            fail("Duplicate validation case ID")
        seen.add(case["id"])
        if set(case["observe"]) - set(cdf["parameters"]):
            fail("Unknown observed CDF parameter")
        if (case["purpose"] == "mode_transition") != bool(case.get("transition")):
            fail("Mode transition requires exactly two declared phases")
        first = phase(cdf, case["parameters"])
        second = phase(cdf, case["transition"]) if case.get("transition") else None
        if second and second["mode"] == first["mode"]:
            fail("Transition must change selector mode")
        result[case["id"]] = {
            **case,
            "position": position,
            "phases": [first] + ([second] if second else []),
            "status": "pending",
        }
    for key, case in result.items():
        if len(encode({key: case})) > TARGET // 2:
            fail("Case definition exceeds 4 KiB; split the suite")
    return result
