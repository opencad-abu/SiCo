"""Validate anchored measurement reports and explicit recipes before specification comparison."""

from .result_contract import ResultError, canonical, coordinate, finite, text, validate_contract
from .waveform_pair import MATCH_KEYS
from .waveform_spec_kinds import check_recipe, report_kind, signal_roles


def check_contract(contract):
    # Share the original inclusive/exclusive bounds, coverage and report-budget checks.
    translated = dict(
        source=contract["source"],
        expected_test_points=contract["expected_test_points"],
        specifications=[],
    )
    for spec in contract["specifications"]:
        kind = spec["kind"]
        check_recipe(spec["recipe"], kind, spec.get("denominator_floor"))
        if kind.startswith("ac_") and spec["analysis"] != "ac":
            raise ResultError("AC specifications require analysis=ac")
        if set(spec["signals"]) != signal_roles(kind):
            raise ResultError("spec signals must match measurement kind")
        translated["specifications"].append(
            dict(
                test=spec["test"],
                output=spec["recipe"]["id"],
                **{
                    k: spec[k]
                    for k in ("unit", "lower", "upper", "lower_inclusive", "upper_inclusive")
                    if k in spec
                },
            )
        )
    validate_contract(translated)
    if len(canonical(contract)) > 120000:
        raise ResultError("waveform spec contract exceeds 120 KB")


def _source(source, anchor, result_ref):
    if not isinstance(source, dict):
        raise ResultError("measurement source missing")
    if source.get("result_ref") != result_ref or any(
        source.get(k) != anchor[k] for k in ("target", "library_path", "history")
    ):
        raise ResultError("measurement must use the exact anchor result snapshot")
    key = coordinate(source.get("test"), source.get("corner"), source.get("point"))
    tests = [t for t in anchor["tests"] if (t["test"], t["corner"], t["point"]) == key]
    coords = [c for c in anchor["coordinates"] if (c["corner"], c["point"]) == key[1:]]
    if (
        len(tests) != 1
        or len(coords) != 1
        or canonical(source.get("coordinate")) != canonical(coords[0])
    ):
        raise ResultError("measurement coordinate does not match anchor RDB")
    for k in ("analysis", "signal", "results_directory", "reader"):
        text(source.get(k), 4096)
    return key


def collect_reports(store, refs, anchor, result_ref):
    if len(set(refs)) != len(refs):
        raise ResultError("duplicate measurement reference")
    indexed, total_bytes = {}, 0
    for ref in sorted(refs):
        report = store.get(ref)
        total_bytes += len(canonical(report))
        if total_bytes > 16 * 1024 * 1024:
            raise ResultError("measurement report inputs exceed 16 MiB")
        kind, sources = report_kind(report)
        if not isinstance(sources, dict) or set(sources) != signal_roles(kind):
            raise ResultError("measurement report source identity missing")
        keys = [_source(s, anchor, result_ref) for s in sources.values()]
        source = sources["input"] if "input" in sources else sources["signal"]
        if kind.startswith("ac_") and source["analysis"] != "ac":
            raise ResultError("AC report requires analysis=ac")
        if "input" in sources and any(
            canonical(sources["input"].get(k)) != canonical(sources["output"].get(k))
            for k in MATCH_KEYS
        ):
            raise ResultError("pair report source mismatch")
        rows, recipes = report.get("rows"), report.get("recipes")
        if (
            not isinstance(rows, list)
            or not isinstance(recipes, list)
            or not 1 <= len(rows) == len(recipes) <= 32
        ):
            raise ResultError("measurement report rows/recipes incomplete")
        by_id = {}
        for recipe in recipes:
            check_recipe(recipe, kind, report.get("denominator_floor"))
            if recipe["id"] in by_id:
                raise ResultError("duplicate recipe id in measurement report")
            by_id[recipe["id"]] = recipe
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or row.get("id") not in by_id or row["id"] in seen:
                raise ResultError("measurement row identity invalid")
            seen.add(row["id"])
            recipe = by_id[row["id"]]
            if (
                row.get("operation") != recipe["operation"]
                or row.get("unit") != recipe["result_unit"]
            ):
                raise ResultError("measurement row/recipe mismatch")
            status = row.get("status")
            if (
                status not in {"scalar", "missing", "error"}
                or (
                    status == "scalar"
                    and (not finite(row.get("value")) or row.get("reason") is not None)
                )
                or (status != "scalar" and row.get("value") is not None)
            ):
                raise ResultError("invalid measurement status/value")
            if status != "scalar":
                text(row.get("reason"), 1024)
            key = (*keys[0], row["id"])
            if key in indexed:
                raise ResultError(
                    "ambiguous duplicate metric at test/corner/point; select one report"
                )
            indexed[key] = dict(
                measurement_ref=ref,
                recipe=recipe,
                denominator_floor=report.get("denominator_floor"),
                row=row,
                kind=kind,
                analysis=source["analysis"],
                sources=sources,
                signals={k: s["signal"] for k, s in sources.items()},
            )
    return indexed
