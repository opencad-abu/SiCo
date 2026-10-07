"""Deterministic sweep contract; observed RDB coordinates are checked independently."""

from itertools import product

from .circuit_spec_schema import CircuitSpecError, unique


def number_text(value):
    return format(float(value), ".15g")


def dimension_plan(recipe):
    corners, sweeps = recipe.get("corners", []), recipe.get("sweeps", [])
    unique(corners, "name", "corners")
    unique(sweeps, "variable", "sweeps")
    globals_ = set(recipe["variables"])
    local = {v for t in recipe["tests"] for v in t["variables"]}
    swept = {s["variable"] for s in sweeps}
    for sweep in sweeps:
        name, values = sweep["variable"], sweep["values"]
        if name not in globals_ or name in local:
            raise CircuitSpecError(
                "sweep requires a declared global without test override: " + name
            )
        for i, value in enumerate(values):
            if any(abs(value - v) <= max(abs(v) * 1e-12, 1e-30) for v in values[:i]):
                raise CircuitSpecError("duplicate/indistinguishable sweep values: " + name)
    for corner in corners:
        if corner["name"].lower() == "nominal":
            raise CircuitSpecError("Nominal is reserved; omit corners for nominal execution")
        names = set(corner["variables"])
        if "temperature" in names or names - globals_ or names & (local | swept):
            raise CircuitSpecError(
                "corner variables require declared globals without test/sweep override"
            )
        models = corner["models"]
        if len({(m["path"], m.get("section")) for m in models}) != len(models):
            raise CircuitSpecError("duplicate corner model inclusion")
    if corners and any(t["models"] for t in recipe["tests"]):
        raise CircuitSpecError(
            "explicit corners supply the complete shared model list; test.models must be empty"
        )
    count = 1
    for sweep in sweeps:
        count *= len(sweep["values"])
    if count > 64 or count * max(1, len(corners)) * len(recipe["tests"]) > 256:
        raise CircuitSpecError("recipe exceeds 64 sweep points or 256 test/corner/points")
    conditions = [
        dict(zip([s["variable"] for s in sweeps], values))
        for values in product(*(s["values"] for s in sweeps))
    ]
    return {
        "expected_test_points": [
            dict(test=t["name"], corner=c, point=p)
            for p in range(1, count + 1)
            for c in ([c["name"] for c in corners] or ["nominal"])
            for t in recipe["tests"]
        ],
        "sweep_combinations": conditions,
        "point_assignment": "verify RDB tuples; preview does not assign tuples to point IDs",
    }


def native_dimensions(recipe):
    return [
        [
            [
                c["name"],
                str(c["temperature_c"]),
                [[k, number_text(v)] for k, v in sorted(c["variables"].items())],
                [[m["path"]] + ([m["section"]] if "section" in m else []) for m in c["models"]],
            ]
            for c in recipe.get("corners", [])
        ],
        [
            [s["variable"], " ".join(number_text(v) for v in s["values"]), s["values"]]
            for s in recipe.get("sweeps", [])
        ],
    ]
