"""Offline qualification of real/AC measurements against explicit specifications."""

from .circuit_spec_schema import CircuitSpecError, validate
from .result_contract import ResultError, canonical, compare_bounds, digest
from .result_tools import ResultStore
from .waveform_measure import MeasurementIssue
from .waveform_spec_inputs import check_contract, collect_reports
from .waveform_spec_kinds import comparison_value
from .waveform_spec_schema import SPEC_REPORT_SCHEMA, SPEC_TOOLS

STATUSES = ("pass", "fail", "missing", "error")


def _counts(rows):
    return {s: sum(r["status"] == s for r in rows) for s in STATUSES}


def _row(spec, key, test, measured):
    row = dict(
        test=key[0],
        corner=key[1],
        point=key[2],
        metric=spec["recipe"]["id"],
        status="missing",
        reason=None,
        value=None,
        unit=spec["unit"],
        margin=None,
        measurement_ref=None,
        measurement_status=None,
        measurement_reason=None,
        raw_value=None,
        raw_unit=None,
        sources=None,
        components=None,
    )
    if measured:
        original = measured["row"]
        row.update(
            measurement_ref=measured["measurement_ref"],
            measurement_status=original["status"],
            measurement_reason=original["reason"],
            raw_value=original["value"],
            raw_unit=original["unit"],
            sources=measured["sources"],
            components=original.get("components"),
        )
        if measured["kind"].startswith("ac_"):
            row["denominator_floor"] = measured["denominator_floor"]
    if not test:
        row["reason"] = "test_point_missing"
    elif test["status"] != "done":
        row["reason"] = "test_not_done"
    elif not measured:
        row["reason"] = "measurement_missing"
    elif (
        measured["kind"] != spec["kind"]
        or measured["analysis"] != spec["analysis"]
        or measured["signals"] != spec["signals"]
    ):
        row.update(status="error", reason="measurement_source_mismatch")
    elif canonical(measured["recipe"]) != canonical(spec["recipe"]):
        row.update(status="error", reason="measurement_recipe_mismatch")
    elif canonical(measured["denominator_floor"]) != canonical(spec.get("denominator_floor")):
        row.update(status="error", reason="measurement_denominator_floor_mismatch")
    elif measured["row"]["status"] != "scalar":
        row.update(status=measured["row"]["status"], reason=measured["row"]["reason"])
    else:
        try:
            value = comparison_value(row["raw_value"], row["raw_unit"], spec["unit"], spec["kind"])
            status, margin = compare_bounds(value, spec)
            row.update(value=value, margin=margin, status=status)
        except MeasurementIssue as exc:
            row.update(status="error", reason=exc.reason)
        except ResultError:
            row.update(status="error", reason="specification_margin_out_of_range")
    return row


def evaluate_waveforms(store, args):
    contract = args["contract"]
    check_contract(contract)
    snapshot = store.get(args["result_ref"])
    indexed = collect_reports(store, args["measurement_refs"], snapshot, args["result_ref"])
    expected = {(p["test"], p["corner"], p["point"]) for p in contract["expected_test_points"]}
    tests = {(p["test"], p["corner"], p["point"]): p for p in snapshot["tests"]}
    missing, unexpected = sorted(expected - set(tests)), sorted(set(tests) - expected)
    rows, summaries, consumed = [], [], set()
    for spec in contract["specifications"]:
        metric = spec["recipe"]["id"]
        selected = []
        for key in sorted(k for k in expected if k[0] == spec["test"]):
            lookup = (*key, metric)
            selected.append(_row(spec, key, tests.get(key), indexed.get(lookup)))
            if lookup in indexed:
                consumed.add(lookup)
        measured = [r for r in selected if r["margin"] is not None]
        worst = (
            min(measured, key=lambda r: (r["margin"], r["status"] != "fail")) if measured else None
        )
        summaries.append(
            dict(
                test=spec["test"],
                metric=metric,
                unit=spec["unit"],
                recipe_sha256=digest(spec["recipe"]),
                counts=_counts(selected),
                worst=worst,
            )
        )
        if spec["kind"].startswith("ac_"):
            summaries[-1]["measurement_identity_sha256"] = digest(
                {
                    k: spec[k]
                    for k in ("kind", "analysis", "signals", "recipe", "denominator_floor")
                    if k in spec
                }
            )
        rows.extend(selected)
    counts = _counts(rows)
    complete = not (missing or unexpected or counts["missing"] or counts["error"])
    verdict = "fail" if counts["fail"] else "pass" if complete else "incomplete"
    report = dict(
        schema=SPEC_REPORT_SCHEMA,
        result_ref=args["result_ref"],
        measurement_refs=sorted(args["measurement_refs"]),
        contract=contract,
        contract_sha256=digest(contract),
        target=snapshot["target"],
        history=snapshot["history"],
        library_path=snapshot["library_path"],
        coordinates=snapshot["coordinates"],
        qualification=verdict,
        spec_qualified={"pass": True, "fail": False, "incomplete": None}[verdict],
        qualification_scope="explicit_contract_only",
        coverage_complete=complete,
        missing_test_points=missing,
        unexpected_test_points=unexpected,
        unselected_measurements=len(indexed) - len(consumed),
        counts=counts,
        summaries=summaries,
        rows=rows,
        unit_policy="native_measurement_to_explicit_spec; SI_prefix_or_identity",
    )
    if any(s["kind"].startswith("ac_") for s in contract["specifications"]):
        report.update(
            stability_qualified=None,
            unit_policy="native_measurement_to_explicit_spec; SI_prefix_or_identity; "
            "AC_deg_rad_without_wrapping; no_dB_linear_conversion",
        )
    return report


def call_waveform_specs(name, args, *, workspace):
    schema = next(t["inputSchema"] for t in SPEC_TOOLS if t["name"] == name)
    try:
        validate(args, schema)
    except CircuitSpecError as exc:
        raise ResultError(str(exc)) from exc
    store = ResultStore(workspace)
    if name == "evaluate_waveform_specs":
        report = evaluate_waveforms(store, args)
        ref, artifact = store.put("wave_specs", report)
        return dict(
            ok=True,
            report_ref=ref,
            artifact=artifact,
            **{
                k: v
                for k, v in report.items()
                if k not in {"schema", "rows", "coordinates", "contract", "measurement_refs"}
            },
        )
    report = store.get(args["report_ref"])
    entity = args.get("entity", "rows")
    filters = {k: args[k] for k in ("test", "metric", "corner", "point", "status") if k in args}
    allowed = {
        "rows": {"test", "metric", "corner", "point", "status"},
        "summaries": {"test", "metric"},
        "coordinates": {"corner", "point"},
    }[entity]
    if set(filters) - allowed:
        raise ResultError("filter not applicable to report entity")
    rows = [r for r in report[entity] if all(r[k] == v for k, v in filters.items())]
    start, limit = args.get("offset", 0), args.get("limit", 30)
    return dict(
        ok=True,
        report_ref=args["report_ref"],
        entity=entity,
        total=len(rows),
        items=rows[start : start + limit],
        next_offset=start + limit if start + limit < len(rows) else None,
    )
