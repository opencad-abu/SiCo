from __future__ import annotations

from typing import Any, Mapping

from .errors import EnvironmentError

from .m1ai_correlation_evidence import _numeric, _validate_evidence

def evaluate_correlation(
    spectre_payload: Mapping[str, Any],
    rnm_payload: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare normalized Spectre and RNM measurements using the checked-in policy."""
    spectre = _validate_evidence(spectre_payload, role="spectre", policy=policy)
    rnm = _validate_evidence(rnm_payload, role="rnm", policy=policy)
    spectre_cases = spectre["cases"]
    rnm_cases = rnm["cases"]
    if set(spectre_cases) != set(rnm_cases):
        missing_in_rnm = sorted(set(spectre_cases) - set(rnm_cases))
        missing_in_spectre = sorted(set(rnm_cases) - set(spectre_cases))
        raise EnvironmentError(
            "Spectre/RNM case ids do not match: "
            f"missing_in_rnm={missing_in_rnm}, missing_in_spectre={missing_in_spectre}"
        )
    if spectre["stimulus"] != rnm["stimulus"]:
        raise EnvironmentError("Spectre/RNM stimulus vectors or sampling policy do not match")

    results: list[dict[str, Any]] = []
    failure_count = 0
    for case_id in sorted(spectre_cases):
        left, right = spectre_cases[case_id], rnm_cases[case_id]
        if left["type"] != right["type"] or left["corner"] != right["corner"]:
            raise EnvironmentError(f"case metadata mismatch for {case_id}")
        metric_results: dict[str, Any] = {}
        for name, rule in policy["metrics"].items():
            sv = left["measurements"][name]
            rv = right["measurements"][name]
            if rule["kind"] == "categorical":
                allowed = set(str(value) for value in rule.get("allowed_values", []))
                if str(sv) not in allowed or str(rv) not in allowed:
                    raise EnvironmentError(f"case {case_id} metric {name} has invalid enum")
                passed = str(sv) == str(rv)
                metric_results[name] = {
                    "kind": "categorical",
                    "spectre": str(sv),
                    "rnm": str(rv),
                    "passed": passed,
                }
            else:
                spectre_value = _numeric(sv, label=f"case {case_id} Spectre {name}")
                rnm_value = _numeric(rv, label=f"case {case_id} RNM {name}")
                error = abs(spectre_value - rnm_value)
                tolerance = float(rule["absolute_tolerance"])
                passed = error <= tolerance
                metric_results[name] = {
                    "kind": "numeric",
                    "unit": rule.get("unit", ""),
                    "spectre": spectre_value,
                    "rnm": rnm_value,
                    "absolute_error": error,
                    "absolute_tolerance": tolerance,
                    "passed": passed,
                }
            failure_count += int(not metric_results[name]["passed"])
        results.append(
            {
                "id": case_id,
                "type": left["type"],
                "corner": left["corner"],
                "metrics": metric_results,
                "passed": all(item["passed"] for item in metric_results.values()),
            }
        )
    return {
        "status": "PASS" if failure_count == 0 else "FAIL_CORRELATION",
        "case_count": len(results),
        "metric_failure_count": failure_count,
        "cases": results,
        "spectre_source": spectre["source_run"],
        "rnm_source": rnm["source_run"],
    }

__all__=["evaluate_correlation"]
