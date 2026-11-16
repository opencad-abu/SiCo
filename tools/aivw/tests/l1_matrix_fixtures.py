from __future__ import annotations

import itertools
import json


def _matrix() -> dict[str, object]:
    return {
        "kind": "exact_cross_product",
        "require_exact_cross_product": True,
        "dimensions": [
            {"name": "process", "source": "corner", "application": "context", "values": ["tt", "ss"]},
            {"name": "temperature_c", "source": "corner", "application": "context", "values": [0, 80]},
            {"name": "vdd_high", "source": "corner", "application": "model_parameter", "values": [1.1, 1.3]},
            {"name": "vector", "source": "vector_id", "application": "stimulus", "values": ["positive", "negative"]},
        ],
    }


def _cases() -> list[dict[str, object]]:
    return [
        {
            "id": f"case-{index:02d}",
            "corner": {"process": process, "temperature_c": temperature, "vdd_high": vdd},
            "vector_id": vector,
        }
        for index, (process, temperature, vdd, vector) in enumerate(
            itertools.product(("tt", "ss"), (0, 80), (1.1, 1.3), ("positive", "negative"))
        )
    ]


def _matrix_event(value: dict[str, object]) -> str:
    return "AIVW_L1_EVENT " + json.dumps(value, sort_keys=True, separators=(",", ":"))


def _plan_for_matrix_event(cases: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "protocol": "AIVW_L1_EVENT v1 JSONL",
        "minimum_case_coverage": 1.0,
        "required_metrics": ["decision", "out"],
        "required_checks": ["initial-state"],
        "cases": cases,
    }


def _matrix_event_plan() -> dict[str, object]:
    cases = []
    for index, (process, vector) in enumerate(
        itertools.product(("tt", "ss"), ("positive", "negative"))
    ):
        cases.append({
            "id": f"matrix-{index}",
            "type": "positive_differential" if vector == "positive" else "negative_differential",
            "region": "deterministic",
            "corner": {"process": process},
            "vector_id": vector,
            "metrics": {
                "decision": {"kind": "categorical", "expected": vector},
                "out": {"kind": "numeric", "expected": 1.2, "absolute_tolerance": 0.01},
            },
            "required_assertions": ["decision", "out"],
        })
    return {
        **_plan_for_matrix_event(cases),
        "matrix": {
            "kind": "exact_cross_product",
            "require_exact_cross_product": True,
            "expected_point_count": 4,
            "dimensions": [
                {"name": "process", "source": "corner", "application": "context", "values": ["tt", "ss"]},
                {"name": "vector", "source": "vector_id", "application": "stimulus", "values": ["positive", "negative"]},
            ],
        },
    }


def _matrix_event_log(plan: dict[str, object]) -> str:
    lines = [_matrix_event({"event": "check", "id": "initial-state", "status": "PASS"})]
    for case in plan["cases"]:
        lines.append(_matrix_event({
            "event": "case", "id": case["id"], "type": case["type"], "region": case["region"],
            "corner": case["corner"], "vector_id": case["vector_id"], "status": "PASS",
            "passed_assertions": ["decision", "out"], "metrics": {"decision": case["vector_id"], "out": 1.2},
        }))
    lines.append(_matrix_event({
        "event": "summary", "status": "PASS", "case_count": 4, "check_count": 1,
        "assertion_count": 8, "matrix_point_count": 4,
    }))
    return "\n".join(lines)
