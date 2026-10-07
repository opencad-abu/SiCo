"""Measurement kind adapters; circuit/project defaults never belong in this layer."""

import math

from .ac_measure import validate_recipes as validate_ac_recipes
from .ac_response import check_recipes as validate_response_recipes
from .ac_response_schema import RESPONSE_RECIPE, RESPONSE_SCHEMA
from .ac_schema import AC_MEASURE_SCHEMA, AC_RECIPE
from .circuit_spec_schema import CircuitSpecError, validate
from .result_contract import ResultError, finite
from .waveform_measure import MeasurementIssue, convert, validate_recipe
from .waveform_pair import validate_pair_recipes
from .waveform_schema import MEASUREMENT_SCHEMA, PAIR_RECIPE, PAIR_SCHEMA, QUANTITY, RECIPE

RECIPE_SCHEMAS = dict(
    single=RECIPE,
    pair=PAIR_RECIPE,
    ac_signal=AC_RECIPE,
    ac_transfer=AC_RECIPE,
    ac_response=RESPONSE_RECIPE,
)
PAIRED_AC = {"ac_transfer", "ac_response"}


def signal_roles(kind):
    return {"signal"} if kind in {"single", "ac_signal"} else {"input", "output"}


def check_recipe(recipe, kind, floor=None):
    try:
        validate(recipe, RECIPE_SCHEMAS[kind])
        if kind in PAIRED_AC:
            validate(floor, QUANTITY)
            if floor["value"] < 0:
                raise ResultError("denominator_floor must be nonnegative")
        elif floor is not None:
            raise ResultError("top-level denominator_floor is only valid for paired AC")
    except (CircuitSpecError, KeyError) as exc:
        raise ResultError(str(exc)) from exc
    if kind == "single":
        validate_recipe(recipe)
    elif kind == "pair":
        validate_pair_recipes([recipe])
    elif kind == "ac_response":
        validate_response_recipes([recipe], floor)
    else:
        validate_ac_recipes([recipe])


def report_kind(report):
    schema = report["schema"]
    if schema == MEASUREMENT_SCHEMA:
        return "single", {"signal": report.get("source")}
    if schema == PAIR_SCHEMA:
        return "pair", report.get("sources")
    if schema == RESPONSE_SCHEMA:
        return "ac_response", report.get("sources")
    if schema == AC_MEASURE_SCHEMA and report.get("kind") in {"signal", "transfer"}:
        return "ac_" + report["kind"], report.get("sources")
    raise ResultError("expected a real or AC measurement report")


def comparison_value(value, source, target, kind):
    # Continuous margins retain their signed branch; unit conversion never wraps phase.
    if kind.startswith("ac_") and source in {"deg", "rad"} and target in {"deg", "rad"}:
        result = (
            value
            if source == target
            else (math.degrees(value) if target == "deg" else math.radians(value))
        )
        if not finite(result):
            raise MeasurementIssue("error", "numeric_overflow")
        return result
    return convert(value, source, target)
