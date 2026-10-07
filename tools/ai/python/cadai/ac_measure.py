"""Frequency-linear Cartesian interpolation and explicit phasor/transfer measurements."""

import math
from bisect import bisect_left

from .ac_schema import AC_SCHEMA
from .measurement_recipe_fields import recipe_fields
from .result_contract import ResultError, finite
from .waveform_measure import UNITS, MeasurementIssue, convert
from .waveform_pair import validate_pair
from .waveform_schema import MAX_SAMPLES


def validate_ac_samples(samples):
    if not isinstance(samples, list) or len(samples) > MAX_SAMPLES:
        raise ResultError("invalid AC sample list/limit")
    previous = -1
    for row in samples:
        if not isinstance(row, list) or len(row) != 3 or not all(finite(v) for v in row):
            raise ResultError("AC sample requires finite frequency/real/imaginary values")
        if row[0] < 0 or row[0] <= previous:
            raise ResultError("AC frequency must be nonnegative and strictly increasing")
        previous = row[0]


def validate_ac(first, second=None):
    for wave in (first,) if second is None else (first, second):
        if wave.get("representation") != "cartesian_complex":
            raise ResultError("AC artifact representation missing")
        if wave["status"] == "waveform" and wave.get("native_y_type") not in {
            "doublecomplex",
            "double",
            "intlong",
        }:
            raise ResultError("AC native vector type missing")
        if wave.get("source", {}).get("analysis") != "ac":
            raise ResultError("AC measurement requires raw ac analysis")
    validate_pair(
        first,
        first if second is None else second,
        schema=AC_SCHEMA,
        sample_validator=validate_ac_samples,
    )


def validate_recipes(recipes):
    if len({r["id"] for r in recipes}) != len(recipes):
        raise ResultError("duplicate AC recipe id")
    for recipe in recipes:
        if set(recipe) != recipe_fields("ac_signal", recipe["operation"]):
            raise ResultError("AC recipe has missing or inapplicable fields")
        for name in ("magnitude_floor", "reference"):
            if name in recipe and (
                recipe[name]["value"] < 0 or (name == "reference" and recipe[name]["value"] == 0)
            ):
                raise ResultError("reference must be positive; magnitude_floor nonnegative")


def _unit(unit, dimension, reason):
    if unit not in UNITS or UNITS[unit][0] not in dimension:
        raise MeasurementIssue("error", reason)
    base = UNITS[unit][0]
    return "1" if base == "dimensionless" else base


def sample(wave, at, role):
    if wave["status"] != "waveform" or not wave["samples"]:
        reason = "source_" + wave["status"] if wave["status"] != "waveform" else "empty_waveform"
        raise MeasurementIssue("missing", role + ":" + reason)
    _unit(wave["x_unit"], {"Hz"}, "frequency_axis_unit_incompatible")
    _unit(at["unit"], {"Hz"}, "frequency_unit_incompatible")
    unit = _unit(wave["y_unit"], {"V", "A", "dimensionless"}, "phasor_unit_incompatible")
    frequency = convert(at["value"], at["unit"], wave["x_unit"])
    rows = wave["samples"]
    if frequency < rows[0][0] or frequency > rows[-1][0]:
        raise MeasurementIssue("missing", role + ":out_of_range")
    index = bisect_left([r[0] for r in rows], frequency)
    a = b = rows[index]
    fraction = 0.0
    if a[0] != frequency:
        a = rows[index - 1]
        fraction = (frequency - a[0]) / (b[0] - a[0])
    values = [
        convert((1 - fraction) * a[i] + fraction * b[i], wave["y_unit"], unit) for i in (1, 2)
    ]
    component = dict(
        real=values[0],
        imaginary=values[1],
        unit=unit,
        frequency_hz=convert(at["value"], at["unit"], "Hz"),
        bracket=[a[0], b[0]],
        bracket_unit=wave["x_unit"],
        fraction=fraction,
    )
    return complex(*values), component


def _scalar(z, unit, recipe, components):
    magnitude = abs(z)
    if not all(finite(v) for v in (z.real, z.imag, magnitude)):
        raise MeasurementIssue("error", "numeric_overflow")
    components["phasor"] = dict(real=z.real, imaginary=z.imag, magnitude=magnitude, unit=unit)
    op = recipe["operation"]
    if op == "magnitude":
        return convert(magnitude, unit, recipe["result_unit"])
    if op == "magnitude_db":
        if recipe["result_unit"] != "dB":
            raise MeasurementIssue("error", "db_result_unit_required")
        reference = recipe["reference"]
        reference = convert(reference["value"], reference["unit"], unit)
        components["reference"] = dict(value=reference, unit=unit)
        if magnitude == 0 or reference <= 0:
            raise MeasurementIssue("error", "db_requires_positive_magnitude_and_reference")
        # Difference of logs avoids under/overflow in a very large or small ratio.
        return 20 * (math.log10(magnitude) - math.log10(reference))
    if recipe["result_unit"] not in {"deg", "rad"}:
        raise MeasurementIssue("error", "phase_unit_must_be_deg_or_rad")
    floor = recipe["magnitude_floor"]
    floor = convert(floor["value"], floor["unit"], unit)
    components["magnitude_floor"] = dict(value=floor, unit=unit)
    if magnitude <= floor:
        raise MeasurementIssue("error", "phase_magnitude_at_or_below_floor")
    angle = math.atan2(z.imag, z.real)
    if angle <= -math.pi:
        angle = math.pi
    return math.degrees(angle) if recipe["result_unit"] == "deg" else angle


def measure_ac(first, recipes, *, second=None, denominator_floor=None):
    validate_ac(first, second)
    validate_recipes(recipes)
    if second is not None and denominator_floor["value"] < 0:
        raise ResultError("denominator_floor must be nonnegative")
    rows = []
    for recipe in recipes:
        components = {}
        row = dict(
            id=recipe["id"],
            operation=recipe["operation"],
            unit=recipe["result_unit"],
            value=None,
            status="scalar",
            reason=None,
            components=components,
        )
        try:
            z, component = sample(first, recipe["at"], "input" if second is not None else "signal")
            components["input" if second is not None else "signal"] = component
            unit = component["unit"]
            if second is not None:
                output, components["output"] = sample(second, recipe["at"], "output")
                if components["output"]["unit"] != unit:
                    raise MeasurementIssue("error", "transfer_unit_incompatible")
                floor = convert(denominator_floor["value"], denominator_floor["unit"], unit)
                components["denominator_floor"] = dict(value=floor, unit=unit)
                if abs(z) <= floor:
                    raise MeasurementIssue("error", "denominator_at_or_below_floor")
                z, unit = output / z, "1"
            row["value"] = _scalar(z, unit, recipe, components)
            if not finite(row["value"]):
                raise MeasurementIssue("error", "numeric_overflow")
        except MeasurementIssue as exc:
            row.update(value=None, status=exc.status, reason=exc.reason)
        rows.append(row)
    return rows
