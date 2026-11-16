from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from aivworkbench.l1 import build_l1_test_plan
from aivworkbench.plugins.xcelium_evidence import ADAPTER_NAME, get_xcelium_evidence_adapter, qualified_model_parameter_routes
from aivworkbench.recipe import load_builtin_recipe


def test_comparator_routes_reuse_instances_by_model_parameter_tuple() -> None:
    recipe = load_builtin_recipe("qualification.comparator_raw_evidence_matrix")
    plan = build_l1_test_plan(recipe.payload["verification"])
    routes = qualified_model_parameter_routes(plan)

    assert routes is not None
    assert routes["instance_count"] == 2
    assert routes["case_instances"]["matrix-00"] == "dut_matrix_0000"
    assert routes["case_instances"]["matrix-01"] == "dut_matrix_0000"
    assert routes["case_instances"]["matrix-02"] == "dut_matrix_0001"
    assert routes["case_instances"]["matrix-03"] == "dut_matrix_0001"
    assert routes["case_instances"]["matrix-00"] == routes["case_instances"]["matrix-13"]
    assert routes["case_instances"]["matrix-00"] != routes["case_instances"]["matrix-02"]
    assert routes["case_routes"]["matrix-00"] == {
        "adapter": ADAPTER_NAME,
        "instance": "dut_matrix_0000",
        "parameters": {"vdd_high": 1.1},
        "sv_overrides": {"VDD_HIGH": 1.1},
    }
    assert routes["case_routes"]["matrix-02"]["sv_overrides"] == {
        "VDD_HIGH": 1.3
    }
    assert routes["dimensions"] == [{"name": "vdd_high", "sv_parameter": "VDD_HIGH"}]
    assert [len(item["case_ids"]) for item in routes["instances"]] == [8, 8]
def test_comparator_route_verifier_rejects_missing_or_forged_routes() -> None:
    recipe = load_builtin_recipe("qualification.comparator_raw_evidence_matrix")
    plan = build_l1_test_plan(recipe.payload["verification"])
    adapter = get_xcelium_evidence_adapter(ADAPTER_NAME)
    assert adapter is not None
    routes = qualified_model_parameter_routes(plan)
    assert routes is not None

    events = [
        {
            "event": "case",
            "id": case["id"],
            "model_parameter_route": deepcopy(routes["case_routes"][case["id"]]),
        }
        for case in plan["cases"]
    ]
    events.append(
        {
            "event": "summary",
            "status": "PASS",
            "model_parameter_instance_count": routes["instance_count"],
        }
    )

    missing = deepcopy(events)
    missing[0].pop("model_parameter_route")
    missing_findings = adapter.verify_outputs(
        Path("/tmp/does-not-exist"),
        plan,
        {"events": missing},
    )
    assert {
        item["code"] for item in missing_findings
    } >= {"xcelium_matrix_route_mismatch"}

    forged = deepcopy(events)
    forged[2]["model_parameter_route"]["instance"] = "dut_matrix_0000"
    forged_findings = adapter.verify_outputs(
        Path("/tmp/does-not-exist"),
        plan,
        {"events": forged},
    )
    assert "xcelium_matrix_route_mismatch" in {
        item["code"] for item in forged_findings
    }

    wrong_count = deepcopy(events)
    wrong_count[-1]["model_parameter_instance_count"] = 99
    count_findings = adapter.verify_outputs(
        Path("/tmp/does-not-exist"),
        plan,
        {"events": wrong_count},
    )
    assert "xcelium_matrix_instance_count_mismatch" in {
        item["code"] for item in count_findings
    }
@pytest.mark.parametrize(
    "mutate, expected_code",
    (
        (
            lambda route: route["parameters"].__setitem__("vdd_high", 1.2),
            "xcelium_matrix_route_mismatch",
        ),
        (
            lambda route: route["sv_overrides"].__setitem__("VDD_HIGH", 1.2),
            "xcelium_matrix_route_mismatch",
        ),
        (
            lambda route: route.__setitem__("adapter", "other.adapter"),
            "xcelium_matrix_route_mismatch",
        ),
    ),
)
def test_comparator_route_verifier_rejects_route_field_tampering(
    mutate, expected_code: str
) -> None:
    recipe = load_builtin_recipe("qualification.comparator_raw_evidence_matrix")
    plan = build_l1_test_plan(recipe.payload["verification"])
    adapter = get_xcelium_evidence_adapter(ADAPTER_NAME)
    assert adapter is not None
    routes = qualified_model_parameter_routes(plan)
    assert routes is not None
    events = [
        {
            "event": "case",
            "id": case["id"],
            "model_parameter_route": deepcopy(routes["case_routes"][case["id"]]),
        }
        for case in plan["cases"]
    ]
    events.append(
        {
            "event": "summary",
            "status": "PASS",
            "model_parameter_instance_count": routes["instance_count"],
        }
    )
    mutate(events[0]["model_parameter_route"])
    findings = adapter.verify_outputs(Path("/tmp/does-not-exist"), plan, {"events": events})
    assert expected_code in {item["code"] for item in findings}
def test_comparator_route_verifier_rejects_unknown_case_and_duplicate_summary() -> None:
    recipe = load_builtin_recipe("qualification.comparator_raw_evidence_matrix")
    plan = build_l1_test_plan(recipe.payload["verification"])
    adapter = get_xcelium_evidence_adapter(ADAPTER_NAME)
    assert adapter is not None
    routes = qualified_model_parameter_routes(plan)
    assert routes is not None
    events = [
        {
            "event": "case",
            "id": case["id"],
            "model_parameter_route": deepcopy(routes["case_routes"][case["id"]]),
        }
        for case in plan["cases"]
    ]
    events.append(
        {
            "event": "case",
            "id": "unknown-case",
            "model_parameter_route": deepcopy(routes["case_routes"]["matrix-00"]),
        }
    )
    summary = {
        "event": "summary",
        "status": "PASS",
        "model_parameter_instance_count": routes["instance_count"],
    }
    events.extend((summary, deepcopy(summary)))
    findings = adapter.verify_outputs(Path("/tmp/does-not-exist"), plan, {"events": events})
    codes = {item["code"] for item in findings}
    assert {
        "xcelium_matrix_route_unexpected",
        "xcelium_matrix_route_summary_count_mismatch",
    } <= codes
def test_comparator_route_verifier_requires_every_matrix_case_route() -> None:
    recipe = load_builtin_recipe("qualification.comparator_raw_evidence_matrix")
    plan = build_l1_test_plan(recipe.payload["verification"])
    adapter = get_xcelium_evidence_adapter(ADAPTER_NAME)
    assert adapter is not None
    routes = qualified_model_parameter_routes(plan)
    assert routes is not None
    events = [
        {
            "event": "case",
            "id": case["id"],
            "model_parameter_route": deepcopy(routes["case_routes"][case["id"]]),
        }
        for case in plan["cases"]
        if case["id"] != "matrix-15"
    ]
    events.append(
        {
            "event": "summary",
            "status": "PASS",
            "model_parameter_instance_count": routes["instance_count"],
        }
    )
    findings = adapter.verify_outputs(Path("/tmp/does-not-exist"), plan, {"events": events})
    assert {
        item["code"] for item in findings
    } >= {"xcelium_matrix_route_event_count_mismatch"}
@pytest.mark.parametrize("invalid_matrix", ([], "matrix", 1))
def test_comparator_adapter_validate_plan_rejects_non_object_matrix(
    invalid_matrix: object,
) -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    plan = build_l1_test_plan(recipe.payload["verification"])
    plan["matrix"] = invalid_matrix
    adapter = get_xcelium_evidence_adapter(ADAPTER_NAME)
    assert adapter is not None
    findings = adapter.validate_plan(plan, adapter.binding_for_plan(plan))
    assert "xcelium_evidence_matrix_invalid" in {item["code"] for item in findings}
def test_comparator_adapter_verify_outputs_returns_finding_for_invalid_plan() -> None:
    adapter = get_xcelium_evidence_adapter(ADAPTER_NAME)
    assert adapter is not None
    findings = adapter.verify_outputs(
        Path("/tmp/does-not-exist"),
        {"matrix": []},
        {"events": []},
    )
    assert findings[0]["code"] == "xcelium_evidence_matrix_invalid"
def test_comparator_route_verifier_rejects_legacy_route_injection() -> None:
    recipe = load_builtin_recipe("saradc.comparator_new")
    plan = build_l1_test_plan(recipe.payload["verification"])
    adapter = get_xcelium_evidence_adapter(ADAPTER_NAME)
    assert adapter is not None
    evidence = {
        "events": [
            {
                "event": "case",
                "id": plan["cases"][0]["id"],
                "model_parameter_route": {
                    "adapter": ADAPTER_NAME,
                    "instance": "dut_matrix_0000",
                    "parameters": {"vdd_high": 1.1},
                    "sv_overrides": {"VDD_HIGH": 1.1},
                },
            },
            {
                "event": "summary",
                "status": "PASS",
                "model_parameter_instance_count": 1,
            },
        ]
    }
    findings = adapter.verify_outputs(
        Path("/tmp/does-not-exist"), plan, evidence
    )
    codes = {item["code"] for item in findings}
    assert {"xcelium_matrix_route_unexpected", "xcelium_matrix_instance_count_unexpected"} <= codes
