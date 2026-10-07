"""Offline explicit cutoff and loop margin reporting; never a stability verdict."""

import math

from .ac_measure import validate_ac
from .ac_response_math import crossings, grid, interpolate, issue, phase_at
from .ac_response_schema import RESPONSE_SCHEMA, RESPONSE_TOOLS
from .circuit_spec_schema import CircuitSpecError, validate
from .measurement_recipe_fields import recipe_fields
from .result_contract import ResultError, finite
from .result_tools import ResultStore
from .waveform_measure import MeasurementIssue, convert


def check_recipes(recipes, floor):
    if floor["value"] < 0:
        raise ResultError("denominator_floor must be nonnegative")
    if len({r["id"] for r in recipes}) != len(recipes):
        raise ResultError("duplicate response recipe id")
    for r in recipes:
        bandwidth = r["operation"] == "bandwidth"
        if set(r) != recipe_fields("ac_response", r["operation"]):
            raise ResultError("response recipe has missing or inapplicable fields")
        if bandwidth:
            if r["reference_gain"]["unit"] != "1" or r["reference_gain"]["value"] <= 0:
                raise ResultError("reference_gain must be positive and dimensionless")
            if r["drop_db"] <= 0 or r["direction"] != "falling":
                raise ResultError("lowpass cutoff requires positive drop_db and falling direction")
        elif r["magnitude_floor"]["unit"] != "1" or r["magnitude_floor"]["value"] < 0:
            raise ResultError("phase magnitude_floor must be dimensionless and nonnegative")


def measure_response(first, second, recipes, floor):
    validate_ac(first, second)
    check_recipes(recipes, floor)
    rows, all_events = [], []
    for r in recipes:
        row = dict(
            id=r["id"],
            operation=r["operation"],
            unit=r["result_unit"],
            status="scalar",
            value=None,
            reason=None,
            components={},
        )
        components = row["components"]
        try:
            xs, za, zb, limit = grid(first, second, r["window"], floor)
            components.update(
                window_hz=[xs[0], xs[-1]], union_points=len(xs), denominator_floor=limit
            )
            bandwidth = r["operation"] == "bandwidth"
            target = r["reference_gain"]["value"] * 10 ** (-r["drop_db"] / 20) if bandwidth else 1.0
            if not finite(target) or target <= 0:
                issue("threshold_numeric_range")
            if bandwidth:
                convert(1, "Hz", r["result_unit"])
                if abs(zb[0] / za[0]) <= target:
                    issue("lowpass_window_start_not_above_threshold")
            elif r["result_unit"] not in {"deg", "rad"}:
                issue("phase_unit_must_be_deg_or_rad")
            events = crossings(xs, za, zb, target)
            all_events.extend(dict(metric=r["id"], threshold=target, **e) for e in events)
            matches = [
                e for e in events if r["direction"] == "either" or e["direction"] == r["direction"]
            ]
            components.update(
                threshold=target, total_crossings=len(events), matching_crossings=len(matches)
            )
            if len(matches) < r["occurrence"]:
                raise MeasurementIssue("missing", "requested_crossing_not_found")
            selected = matches[r["occurrence"] - 1]
            components["selected_crossing"] = selected
            z = interpolate(xs, zb, selected["frequency_hz"]) / interpolate(
                xs, za, selected["frequency_hz"]
            )
            if not all(finite(v) for v in (z.real, z.imag, abs(z))):
                issue("numeric_overflow")
            if not math.isclose(abs(z), target, rel_tol=1e-8, abs_tol=0):
                issue("crossing_residual_too_large")
            components["transfer"] = dict(real=z.real, imaginary=z.imag, magnitude=abs(z), unit="1")
            if bandwidth:
                row["value"] = convert(selected["frequency_hz"], "Hz", r["result_unit"])
            else:
                angle, path = phase_at(
                    xs,
                    za,
                    zb,
                    selected,
                    r["loop_sign"],
                    r["phase_anchor_turns"],
                    r["magnitude_floor"]["value"],
                )
                components.update(
                    phase_path=path, loop_sign=r["loop_sign"], characteristic="one_plus_L"
                )
                margin = math.pi + angle
                row["value"] = math.degrees(margin) if r["result_unit"] == "deg" else margin
            if not finite(row["value"]):
                issue("numeric_overflow")
        except MeasurementIssue as exc:
            row.update(status=exc.status, reason=exc.reason, value=None)
        except (OverflowError, ZeroDivisionError):
            row.update(status="error", reason="numeric_overflow", value=None)
        rows.append(row)
    return rows, all_events


def call_response(name, args, *, workspace):
    try:
        validate(args, next(t["inputSchema"] for t in RESPONSE_TOOLS if t["name"] == name))
    except CircuitSpecError as exc:
        raise ResultError(str(exc)) from exc
    store = ResultStore(workspace)
    if name == "query_ac_response":
        report = store.get(args["report_ref"])
        entity = args.get("entity", "rows")
        key = "id" if entity == "rows" else "metric"
        rows = [r for r in report[entity] if "metric" not in args or r[key] == args["metric"]]
        start, limit = args.get("offset", 0), args.get("limit", 30)
        return dict(
            ok=True,
            report_ref=args["report_ref"],
            entity=entity,
            total=len(rows),
            items=rows[start : start + limit],
            next_offset=start + limit if start + limit < len(rows) else None,
        )
    first, second = (store.get(args[k]) for k in ("input_ref", "output_ref"))
    rows, events = measure_response(first, second, args["recipes"], args["denominator_floor"])
    report = dict(
        schema=RESPONSE_SCHEMA,
        **args,
        sources=dict(input=first["source"], output=second["source"]),
        rows=rows,
        crossings=events,
        all_measured=all(r["status"] == "scalar" for r in rows),
        spec_qualified=None,
        stability_qualified=None,
        coverage="explicit_frequency_window_only",
        crossing_policy="strict_side_change_in_open_window",
    )
    ref, artifact = store.put("ac_response", report)
    return dict(
        ok=True,
        report_ref=ref,
        artifact=artifact,
        **{k: v for k, v in report.items() if k not in {"schema", "recipes", "crossings"}},
    )
