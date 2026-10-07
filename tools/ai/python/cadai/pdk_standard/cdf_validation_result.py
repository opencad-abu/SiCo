"""Classify observed execution without treating callback correction as legal input."""

from decimal import Decimal

from .constraints import accepts, unknown
from .project import NUMBER, SCALES


def literal(value):
    if isinstance(value, str):
        match = NUMBER.fullmatch(value)
        if match:
            return Decimal(match[1]) * Decimal(str(SCALES[match[2].lower()]))
    return value


def equal(left, right):
    a, b = literal(left), literal(right)
    return a == b


def classify(result, case, artifact):
    requested = (
        case.get("transition") if result.get("stage") == "transition" else case["parameters"]
    )
    if result.get("saved"):
        requested = case.get("transition", case["parameters"])
    trace = result.get("trace") or []
    final = {p["name"]: p.get("current", {}) for p in result.get("parameters", [])}
    phase_trace = trace
    if result.get("stage") == "transition":
        start = next(
            (i for i, r in enumerate(trace) if r["label"] == "transition:before"), len(trace)
        )
        phase_trace = trace[start:]
    attempted = {
        r["label"].split(":", 2)[2] for r in phase_trace if r["label"].startswith("builtin:set:")
    }
    # An executor rejection can still contain evidence that a callback corrected a value.
    evaluated = next(
        (
            r.get("values", {})
            for r in reversed(phase_trace)
            if r["label"].startswith("builtin:set:")
        ),
        {},
    )
    changed = [
        n
        for n, v in requested.items()
        if n in attempted
        and n in evaluated
        and evaluated[n].get("status") == "known"
        and not equal(evaluated[n].get("value"), v)
    ]
    restored = (
        result.get("cdf_values_restored") is True and result.get("cdf_metadata_unchanged") is True
    )
    if (
        result.get("ok")
        and result.get("saved")
        and result.get("readback_verified")
        and result.get("all_requested_match")
        and restored
        and result.get("window_open") is False
    ):
        status = "accepted"
    elif changed and restored and not result.get("saved"):
        status = "adjusted"
    elif result.get("saved") or result.get("window_open"):
        status = "unsafe"
    elif not result.get("callback_execution_attempted"):
        status = "unsupported"
    elif restored:
        status = "rejected"
    else:
        status = "unsafe"
    rows = []
    for n in case["observe"]:
        observed = final.get(n, evaluated.get(n, {"status": "unavailable"}))
        rows.append(
            {
                "name": n,
                "value": observed.get("value") if len(str(observed.get("value"))) <= 256 else None,
                "status": observed.get("status")
                if len(str(observed.get("value"))) <= 256
                else "see_artifact",
                "source": "saved_readback"
                if n in final and result.get("readback_verified")
                else "callback_checkpoint",
            }
        )
    return {
        "status": status,
        "probe_ref": result.get("probe_ref"),
        "stage": result.get("stage"),
        "saved": bool(result.get("saved")),
        "readback_verified": bool(result.get("readback_verified")),
        "source_restored": restored,
        "adjusted_inputs": changed,
        "observed": rows,
        "trace_labels": [s["label"] for s in trace],
        "error": (result.get("error") or "")[:1024],
        "artifact": artifact,
        "callbacks_attempted": bool(result.get("callback_execution_attempted")),
        "window_open": result.get("window_open"),
        "scope": "tested_case_only",
    }


def coverage(cdf, cases):
    from .interface import collection_parameters
    from .policy_modes import rows

    inputs, _ = collection_parameters(cdf)
    modes, _ = rows(cdf)
    accepted = [c for c in cases.values() if c["status"] == "accepted"]
    unobserved = any(
        any(r["status"] != "known" for r in c.get("observed", [])) or not c.get("observed")
        for c in accepted
    )
    sampled = {p["mode"] for c in accepted for p in c["phases"]}
    varied = {
        n
        for n in inputs
        if len({str(p["values"].get(n)) for c in accepted for p in c["phases"] if n in p["active"]})
        >= 2
    }
    missing = []
    if unobserved:
        missing.append("requested_observations_unavailable")
    if any("id" not in r for r in modes):
        missing.append("selector_conditions_unresolved")
    if set(r["id"] for r in modes if "id" in r) - sampled:
        missing.append("some_selector_modes_not_accepted")
    if set(inputs) - varied:
        missing.append("some_inputs_lack_two_accepted_values")
    if not any(c["purpose"] == "baseline" for c in accepted):
        missing.append("baseline_not_accepted")
    if not any(
        c["purpose"] == "boundary"
        for c in cases.values()
        if c["status"] in {"accepted", "adjusted", "rejected"}
    ):
        missing.append("boundary_not_observed")
    if not any(
        c["purpose"] == "off_grid"
        for c in cases.values()
        if c["status"] in {"accepted", "adjusted", "rejected"}
    ):
        missing.append("off_grid_not_observed")
    if len(modes) > 1 and not any(c["purpose"] == "mode_transition" for c in accepted):
        missing.append("mode_transition_not_accepted")
    if any(
        c["status"]
        in {"pending", "running", "uncertain", "cancelled", "unsafe", "unsupported", "failed"}
        for c in cases.values()
    ):
        missing.append("incomplete_or_unsupported_cases")
    if any(
        c["status"] != "accepted"
        for c in cases.values()
        if c["purpose"] in {"baseline", "variation", "mode_transition"}
    ):
        missing.append("nominal_execution_conflict")
    for case in accepted:
        for phase in case["phases"]:
            definitions = cdf["parameters"]
            values = {n: p["default"] for n, p in definitions.items() if not unknown(p["default"])}
            values.update(phase["values"])
            context = {"definitions": definitions, "values": values}
            if any(
                accepts(values[n], definitions[n], context) is False
                for n in phase["active"]
                if n in values
            ):
                missing.append("accepted_sample_conflicts_with_known_constraints")
                break
        if "accepted_sample_conflicts_with_known_constraints" in missing:
            break
    if any(
        cdf["parameters"][n]["domain"]["kind"] in {"unknown", "not_applicable"}
        or unknown(cdf["parameters"][n]["unit"])
        for n in inputs
    ):
        missing.append("input_constraints_still_unknown")
    return {
        "accepted_modes": sorted(sampled),
        "varied_inputs": sorted(varied),
        "missing": missing,
        "execution_eligible": not missing,
        "domain_inferred": False,
        "scope": "declared_cases_and_current_sources",
    }
