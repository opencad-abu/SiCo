"""Piecewise-linear real-waveform recipes, independent of circuit/PDK/test names."""

import math
from bisect import bisect_left, bisect_right

from .measurement_recipe_fields import recipe_fields
from .result_contract import ResultError, finite

# No case folding: mV and MV have different scales. Unknown units allow identity only.
UNITS = {"1": ("dimensionless", 1.0)}
for _base in ("s", "V", "A", "Hz"):
    for _prefix, _scale in (
        ("", 1.0),
        ("f", 1e-15),
        ("p", 1e-12),
        ("n", 1e-9),
        ("u", 1e-6),
        ("m", 1e-3),
        ("k", 1e3),
        ("M", 1e6),
        ("G", 1e9),
    ):
        UNITS[_prefix + _base] = (_base, _scale)


class MeasurementIssue(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason


def convert(value, source, target):
    if not source or not target:
        raise MeasurementIssue("error", "unit_unknown")
    if source == target:
        return value
    if source not in UNITS or target not in UNITS or UNITS[source][0] != UNITS[target][0]:
        raise MeasurementIssue("error", "unit_incompatible")
    result = value * (UNITS[source][1] / UNITS[target][1])
    if not finite(result):
        raise MeasurementIssue("error", "numeric_overflow")
    return result


def validate_recipe(recipe):
    op = recipe["operation"]
    if set(recipe) != recipe_fields("single", op):
        raise ResultError(op + " recipe has missing or inapplicable fields")


def validate_samples(samples):
    if not isinstance(samples, list):
        raise ResultError("waveform samples must be a list")
    previous = None
    for row in samples:
        if not isinstance(row, list) or len(row) != 2 or not all(finite(v) for v in row):
            raise ResultError("non-real/nonfinite waveform sample")
        if previous is not None and row[0] <= previous:
            raise ResultError("waveform axis must be strictly increasing; no sorting/deduplication")
        previous = row[0]


def _sample(xs, ys, x):
    if x < xs[0] or x > xs[-1]:
        raise MeasurementIssue("missing", "out_of_range")
    i = bisect_left(xs, x)
    if xs[i] == x:
        return ys[i]
    fraction = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
    return (1 - fraction) * ys[i - 1] + fraction * ys[i]


def _quantity(q, unit):
    return convert(q["value"], q["unit"], unit)


def _calculate(wave, recipe):
    if wave["status"] != "waveform":
        raise MeasurementIssue("missing", "source_" + wave["status"])
    samples = wave["samples"]
    if not samples:
        raise MeasurementIssue("missing", "empty_waveform")
    xs, ys = [p[0] for p in samples], [p[1] for p in samples]
    xu, yu = wave["x_unit"], wave["y_unit"]
    op = recipe["operation"]
    if op == "sample":
        return convert(_sample(xs, ys, _quantity(recipe["at"], xu)), yu, recipe["result_unit"])
    lo, hi = (_quantity(recipe["window"][k], xu) for k in ("start", "stop"))
    if lo >= hi:
        raise MeasurementIssue("error", "invalid_window")
    endpoints = [_sample(xs, ys, lo), _sample(xs, ys, hi)]
    start, stop = bisect_right(xs, lo), bisect_left(xs, hi)
    wx, wy = [lo] + xs[start:stop] + [hi], [endpoints[0]] + ys[start:stop] + [endpoints[1]]
    if op in {"minimum", "maximum"}:
        result = (min if op == "minimum" else max)(wy)
    elif op in {"mean", "rms"}:
        # Normalize first to avoid squaring large raw amplitudes; integrate each segment exactly.
        scale = max(abs(y) for y in wy) or 1.0
        norm = [y / scale for y in wy]
        terms = []
        for i, (a, b) in enumerate(zip(norm, norm[1:])):
            weight = (wx[i + 1] - wx[i]) / (hi - lo)
            terms.append(weight * ((a + b) / 2 if op == "mean" else (a * a + a * b + b * b) / 3))
        integral = math.fsum(terms)
        result = scale * (integral if op == "mean" else math.sqrt(max(0.0, integral)))
    else:
        threshold = _quantity(recipe["threshold"], yu)
        count = 0
        for i, (a, b) in enumerate(zip(wy, wy[1:])):
            rising, falling = a < threshold <= b, a > threshold >= b
            direction = recipe["direction"]
            if (rising and direction in {"rising", "either"}) or (
                falling and direction in {"falling", "either"}
            ):
                count += 1
                if count == recipe["occurrence"]:
                    fraction = (threshold - a) / (b - a)
                    result = (1 - fraction) * wx[i] + fraction * wx[i + 1]
                    return convert(result, xu, recipe["result_unit"])
        raise MeasurementIssue("missing", "no_crossing")
    return convert(result, yu, recipe["result_unit"])


def measure(wave, recipes):
    validate_samples(wave["samples"])
    rows = []
    for recipe in recipes:
        row = dict(
            id=recipe["id"],
            operation=recipe["operation"],
            unit=recipe["result_unit"],
            status="scalar",
            value=None,
            reason=None,
        )
        try:
            row["value"] = _calculate(wave, recipe)
            if not finite(row["value"]):
                raise MeasurementIssue("error", "numeric_overflow")
        except MeasurementIssue as exc:
            row.update(status=exc.status, reason=exc.reason, value=None)
        rows.append(row)
    return rows
