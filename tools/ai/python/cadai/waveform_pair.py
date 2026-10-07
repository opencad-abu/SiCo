"""Paired scalar recipes with exact coordinate identity and explicit event pairing."""

from .measurement_recipe_fields import recipe_fields
from .result_contract import ResultError, canonical, finite, text
from .waveform_measure import UNITS, MeasurementIssue, convert, measure, validate_samples
from .waveform_schema import MAX_SAMPLES, PAIR_SCHEMA, WAVE_SCHEMA

# Session handles may change on reopen. The immutable result_ref and full coordinate must agree.
MATCH_KEYS = (
    "target",
    "library_path",
    "history",
    "test",
    "corner",
    "point",
    "analysis",
    "coordinate",
    "results_directory",
    "result_ref",
    "reader",
)


def validate_pair(first, second, *, schema=WAVE_SCHEMA, sample_validator=validate_samples):
    for wave in (first, second):
        if wave.get("schema") != schema or not isinstance(wave.get("source"), dict):
            raise ResultError("paired measurement requires waveform artifacts with source identity")
        source = wave["source"]
        if any(k not in source for k in MATCH_KEYS):
            raise ResultError("paired waveform source identity is incomplete")
        for key in (
            "library_path",
            "history",
            "test",
            "corner",
            "analysis",
            "results_directory",
            "result_ref",
            "reader",
            "signal",
        ):
            text(source.get(key), 4096)
        if (
            not isinstance(source["target"], list)
            or len(source["target"]) != 3
            or type(source["point"]) is not int
            or source["point"] < 0
        ):
            raise ResultError("paired waveform target/point is invalid")
        for name in source["target"]:
            text(name)
        coord = source["coordinate"]
        if (
            not isinstance(coord, dict)
            or coord.get("corner") != source["corner"]
            or type(coord.get("point")) is not int
            or coord["point"] != source["point"]
            or not isinstance(coord.get("parameters"), list)
        ):
            raise ResultError("paired waveform coordinate is incomplete or inconsistent")
        if wave.get("status") not in {
            "waveform",
            "scalar",
            "complex",
            "family",
            "missing",
            "error",
        }:
            raise ResultError("invalid source waveform status")
        sample_validator(wave.get("samples"))
        if len(wave["samples"]) > MAX_SAMPLES:
            raise ResultError("waveform sample limit exceeded")
        if wave["status"] != "waveform" and wave["samples"]:
            raise ResultError("non-waveform artifact contains samples")
    if any(canonical(first["source"][k]) != canonical(second["source"][k]) for k in MATCH_KEYS):
        raise ResultError(
            "waveforms must match exact result snapshot/history/test/corner/point/analysis"
        )


def validate_pair_recipes(recipes):
    if len({r["id"] for r in recipes}) != len(recipes):
        raise ResultError("duplicate measurement recipe id")
    for recipe in recipes:
        op = recipe["operation"]
        if set(recipe) != recipe_fields("pair", op):
            raise ResultError(op + " recipe has missing or inapplicable fields")
        if op != "delay" and recipe["denominator_floor"]["value"] < 0:
            raise ResultError("denominator_floor must be nonnegative")


def _component(wave, role, operation, result_unit, **options):
    recipe = dict(
        id=role, operation=operation, interpolation="linear", result_unit=result_unit, **options
    )
    row = measure(wave, [recipe])[0]
    if row["status"] != "scalar":
        raise MeasurementIssue(row["status"], role + ":" + row["reason"])
    return {"value": row["value"], "unit": result_unit}


def _units(first, second, delay):
    for role, wave in (("input", first), ("output", second)):
        if wave["status"] != "waveform" or not wave["samples"]:
            reason = (
                "source_" + wave["status"] if wave["status"] != "waveform" else "empty_waveform"
            )
            raise MeasurementIssue("missing", role + ":" + reason)
    xu, yu = first.get("x_unit"), second.get("x_unit")
    if not xu or not yu:
        raise MeasurementIssue("error", "axis_unit_unknown")
    if xu not in UNITS or yu not in UNITS or UNITS[xu][0] != UNITS[yu][0]:
        raise MeasurementIssue("error", "axis_unit_incompatible")
    if delay:
        if UNITS[xu][0] != "s":
            raise MeasurementIssue("error", "delay_requires_time_axis")
        return "s"
    units = [wave.get("y_unit") for wave in (first, second)]
    if any(not u for u in units):
        raise MeasurementIssue("error", "gain_unit_unknown")
    if (
        any(u not in UNITS for u in units)
        or UNITS[units[0]][0] != UNITS[units[1]][0]
        or UNITS[units[0]][0] not in {"V", "A", "dimensionless"}
    ):
        raise MeasurementIssue("error", "gain_unit_incompatible")
    return "1" if UNITS[units[0]][0] == "dimensionless" else UNITS[units[0]][0]


def _calculate_pair(first, second, recipe, components):
    op = recipe["operation"]
    unit = _units(first, second, op == "delay")
    if op == "delay":
        # Check the requested unit even when an event will be absent.
        convert(1, "s", recipe["result_unit"])
        for role, wave in (("input", first), ("output", second)):
            components[role] = _component(wave, role, "crossing", "s", **recipe[role + "_event"])
        delta = components["output"]["value"] - components["input"]["value"]
        return convert(delta, "s", recipe["result_unit"])
    if recipe["result_unit"] != "1":
        raise MeasurementIssue("error", "gain_result_unit_must_be_1")
    floor = recipe["denominator_floor"]
    limit = convert(floor["value"], floor["unit"], unit)
    operation, field = ("sample", "at") if op == "gain_sample" else ("rms", "window")
    for role, wave in (("input", first), ("output", second)):
        components[role] = _component(wave, role, operation, unit, **{field: recipe[field]})
    components["denominator_floor"] = {"value": limit, "unit": unit}
    denominator = components["input"]["value"]
    if abs(denominator) <= limit:
        raise MeasurementIssue("error", "denominator_at_or_below_floor")
    return components["output"]["value"] / denominator


def measure_pair(first, second, recipes):
    validate_pair(first, second)
    validate_pair_recipes(recipes)
    rows = []
    for recipe in recipes:
        row = dict(
            id=recipe["id"],
            operation=recipe["operation"],
            unit=recipe["result_unit"],
            status="scalar",
            value=None,
            reason=None,
            components={},
        )
        try:
            row["value"] = _calculate_pair(first, second, recipe, row["components"])
            if not finite(row["value"]):
                raise MeasurementIssue("error", "numeric_overflow")
        except MeasurementIssue as exc:
            row.update(status=exc.status, reason=exc.reason, value=None)
        rows.append(row)
    return rows


def pair_report(store, args):
    first, second = (store.get(args[k]) for k in ("input_ref", "output_ref"))
    rows = measure_pair(first, second, args["recipes"])
    report = dict(
        schema=PAIR_SCHEMA,
        input_ref=args["input_ref"],
        output_ref=args["output_ref"],
        sources=dict(input=first["source"], output=second["source"]),
        coordinate_policy="same_result_test_point_analysis",
        recipes=args["recipes"],
        rows=rows,
        all_measured=all(r["status"] == "scalar" for r in rows),
        spec_qualified=None,
    )
    ref, artifact = store.put("pair_measurements", report)
    return dict(
        ok=True,
        measurement_ref=ref,
        artifact=artifact,
        **{k: v for k, v in report.items() if k not in {"schema", "recipes"}},
    )
