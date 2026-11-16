from __future__ import annotations

import json


def _plan() -> dict[str, object]:
    return {
        "schema_version": 1,
        "protocol": "AIVW_L1_EVENT v1 JSONL",
        "minimum_case_coverage": 1.0,
        "required_metrics": ["decision", "out"],
        "required_checks": ["initial-state"],
        "excluded_metrics": [{"name": "delay", "reason": "calibration_required"}],
        "cases": [
            {
                "id": "positive",
                "type": "positive_differential",
                "region": "deterministic",
                "metrics": {
                    "decision": {"kind": "categorical", "expected": "positive"},
                    "out": {
                        "kind": "numeric",
                        "expected": 1.2,
                        "absolute_tolerance": 0.01,
                    },
                },
                "required_assertions": ["decision", "out"],
            }
        ],
    }


def _event(value: dict[str, object]) -> str:
    return "AIVW_L1_EVENT " + json.dumps(value, sort_keys=True, separators=(",", ":"))


def _passing_log() -> str:
    return "\n".join(
        (
            _event({"event": "check", "id": "initial-state", "status": "PASS"}),
            _event(
                {
                    "event": "case",
                    "id": "positive",
                    "type": "positive_differential",
                    "region": "deterministic",
                    "status": "PASS",
                    "passed_assertions": ["decision", "out"],
                    "metrics": {"decision": "positive", "out": 1.2},
                }
            ),
            _event(
                {
                    "event": "summary",
                    "status": "PASS",
                    "case_count": 1,
                    "check_count": 1,
                    "assertion_count": 2,
                }
            ),
        )
    )


def _temporal_plan() -> dict[str, object]:
    plan = _plan()
    plan["temporal_assertions"] = [
        {
            "id": "output_complementary",
            "property": "out + outb == VDD",
            "clock": "posedge clk",
            "severity": "error",
        }
    ]
    return plan


def _temporal_log(
    status: str = "PASS", assertion_id: str = "output_complementary"
) -> str:
    return "\n".join(
        (
            _event({"event": "check", "id": "initial-state", "status": "PASS"}),
            _event(
                {
                    "event": "case",
                    "id": "positive",
                    "type": "positive_differential",
                    "region": "deterministic",
                    "status": "PASS",
                    "passed_assertions": ["decision", "out"],
                    "metrics": {"decision": "positive", "out": 1.2},
                }
            ),
            _event({"event": "temporal", "id": assertion_id, "status": status}),
            _event(
                {
                    "event": "summary",
                    "status": "PASS",
                    "case_count": 1,
                    "check_count": 1,
                    "assertion_count": 2,
                    "temporal_count": 1,
                }
            ),
        )
    )
