from __future__ import annotations

from copy import deepcopy
import json
import math
import re
import pytest

from aivworkbench.rule_template import RuleTemplateEngine
from aivworkbench.ldo import (
    LDOTopologyError,
    canonicalize_interface,
    canonicalize_structure,
    match_interface,
    require_match,
)
from aivworkbench.ldo_metrics import evaluate_metrics
from aivworkbench.model import ModelGenerationRequest
from aivworkbench.plugins.linear_regulator import generate_linear_regulator_candidate
from aivworkbench.recipe import load_builtin_recipe
from aivworkbench.profiles import product_root
from aivworkbench.registry import builtin_registry


def _ports(cell: str):
    names = ["VDD", "VSS"] + (["EN"] if cell == "LDO_AON" else []) + ["VOUT"]
    kinds = {"VDD": "power", "VSS": "ground", "EN": "digital", "VOUT": "power"}
    directions = {"VDD": "inout", "VSS": "inout", "EN": "input", "VOUT": "output"}
    return {name: {"kind": kinds[name], "direction": directions[name]} for name in names}


@pytest.mark.parametrize("cell", ["LDO_MASTER", "LDO_AON"])
def test_interface_and_structure_match_exactly(cell: str) -> None:
    original = {"cell": cell, "port_order": list(_ports(cell)), "ports": _ports(cell)}
    before = deepcopy(original)
    interface = canonicalize_interface(original)
    structure = canonicalize_structure(original)
    assert original == before
    assert require_match(interface, structure).matched
    assert interface.digest == canonicalize_interface(original).digest


@pytest.mark.parametrize(
    ("mutation",),
    [
        (lambda p: p["ports"].update({"EXTRA": {"direction": "input", "kind": "digital"}}),),
        (lambda p: p["ports"]["VDD"].update({"direction": "output"}),),
        (lambda p: p["ports"]["VOUT"].update({"kind": "digital"}),),
        (lambda p: p.update({"port_order": ["VSS", "VDD", "VOUT"]}),),
    ],
)
def test_interface_mismatch_fails_closed(mutation) -> None:
    contract = {"cell": "LDO_MASTER", "port_order": ["VDD", "VSS", "VOUT"], "ports": _ports("LDO_MASTER")}
    structure = deepcopy(contract)
    mutation(structure)
    match = match_interface(contract, structure)
    assert not match.matched
    with pytest.raises(LDOTopologyError):
        require_match(contract, structure)


def test_unsupported_cell_and_extra_enable_are_rejected() -> None:
    with pytest.raises(LDOTopologyError):
        canonicalize_interface({"cell": "LDO_OTHER", "ports": {}})
    contract = {"cell": "LDO_MASTER", "port_order": ["VDD", "VSS", "VOUT"], "ports": _ports("LDO_MASTER")}
    structure = {"cell": "LDO_MASTER", "port_order": ["VDD", "VSS", "EN", "VOUT"], "ports": _ports("LDO_AON")}
    assert not match_interface(contract, structure).matched


def test_metrics_numeric_categorical_timing_waveform_and_blocking() -> None:
    policy = [
        {"name": "vout", "kind": "numeric", "absolute_tolerance": 0.01},
        {"name": "state", "kind": "categorical"},
        {"name": "delay", "kind": "timing", "absolute_tolerance": 0.1},
        {"name": "wave", "kind": "waveform", "absolute_tolerance": 0.02},
    ]
    expected = {"vout": 2.5, "state": "active", "delay": 1.0, "wave": [0.0, 2.5]}
    actual = {"vout": 2.505, "state": "active", "delay": 1.05, "wave": [0.01, 2.49]}
    result = evaluate_metrics(policy, expected, actual)
    assert result.status == "PASS"
    actual["state"] = "inactive"
    assert evaluate_metrics(policy, expected, actual).status == "FAIL_CORRELATION"
    assert evaluate_metrics(policy, expected, None).status == "BLOCKED_EVIDENCE"
    actual["state"] = "active"
    actual["vout"] = float("nan")
    assert evaluate_metrics(policy, expected, actual).code == "BLOCKED_EVIDENCE"


@pytest.mark.parametrize(
    "malformed",
    [
        {"vout": object()},
        {"vout": {"nested": {1: "non-text-key"}}},
        {"vout": {"nested": {"unsupported": object()}}},
        {"vout": {"nested": float("inf")}},
    ],
)
def test_metrics_malformed_evidence_is_always_blocked(malformed) -> None:
    policy = [{"name": "vout", "kind": "categorical"}]
    expected = {"vout": "nominal"}
    result = evaluate_metrics(policy, expected, malformed)
    assert result.status == "BLOCKED_EVIDENCE"
    assert result.code == "BLOCKED_EVIDENCE"


def test_metrics_cyclic_evidence_is_blocked_without_recursion_error() -> None:
    nested = {}
    nested["self"] = nested
    result = evaluate_metrics(
        [{"name": "vout", "kind": "categorical"}],
        {"vout": "nominal"},
        {"vout": nested},
    )
    assert result.status == "BLOCKED_EVIDENCE"
    assert result.code == "BLOCKED_EVIDENCE"


@pytest.mark.parametrize(
    ("expected", "actual", "code"),
    [
        ({"vout": object()}, {"vout": "nominal"}, "BLOCKED_INPUT"),
        ({"vout": {"nested": {1: "bad-key"}}}, {"vout": "nominal"}, "BLOCKED_INPUT"),
        ({"vout": float("inf")}, {"vout": "nominal"}, "BLOCKED_INPUT"),
        ({"vout": [object()]}, {"vout": "nominal"}, "BLOCKED_INPUT"),
    ],
)
def test_metrics_malformed_expected_is_always_blocked(expected, actual, code: str) -> None:
    result = evaluate_metrics(
        [{"name": "vout", "kind": "categorical"}], expected, actual
    )
    assert result.status == code
    assert result.code == code


def test_metrics_malformed_policy_and_assertions_are_classified() -> None:
    assert evaluate_metrics(
        [{"name": "vout", "kind": "numeric", "absolute_tolerance": object()}],
        {"vout": 1.0},
        {"vout": 1.0},
    ).code == "BLOCKED_POLICY"
    assert evaluate_metrics(
        [{"name": "vout", "kind": "categorical"}],
        {"vout": "nominal"},
        {"vout": "nominal"},
        required_assertions="assertion-not-an-array",
    ).code == "BLOCKED_INPUT"
    assert evaluate_metrics(
        [{"name": "vout", "kind": "categorical"}],
        {"vout": "nominal"},
        {"vout": "nominal"},
        passed_assertions=[object()],
    ).code == "BLOCKED_EVIDENCE"


def test_metrics_malformed_expected_numeric_and_waveform_do_not_leak_typeerror() -> None:
    numeric = evaluate_metrics(
        [{"name": "vout", "kind": "numeric"}], {"vout": "not-a-number"}, {"vout": 1.0}
    )
    assert numeric.status == "BLOCKED_INPUT"
    waveform = evaluate_metrics(
        [{"name": "wave", "kind": "waveform"}], {"wave": ["bad"]}, {"wave": [1.0]}
    )
    assert waveform.status == "BLOCKED_INPUT"


def test_metrics_accepts_complete_correlation_policy_and_freezes_results() -> None:
    recipe = load_builtin_recipe("ldo.linear_regulator")
    policy = json.loads((product_root() / "recipes/ldo/correlation_policy.json").read_text())
    expected = {
        "vout": 2.5,
        "supply_current": 0.005,
        "enable_state": "active",
        "settling_time": 1.0e-6,
        "vout_waveform": [0.0, 2.5],
    }
    actual = deepcopy(expected)
    result = evaluate_metrics(policy, expected, actual)
    assert result.status == "PASS"
    # A frozen result must not retain aliases into either input or nested
    # observation/summary payloads.
    actual["vout_waveform"][0] = 99.0
    assert result.observations[-1].actual[0] == 0.0
    with pytest.raises(TypeError):
        result.observations[-1].actual[0] = 1.0
    with pytest.raises(TypeError):
        result.summary["missing_assertions"] = ("tampered",)
    exported = result.to_dict()
    exported["observations"][-1]["actual"][0] = 77.0
    exported["summary"]["missing_assertions"].append("tampered")
    assert result.to_dict()["observations"][-1]["actual"][0] == 0.0
    assert result.to_dict()["summary"]["missing_assertions"] == []


def test_list_shaped_interface_infers_order_but_structure_order_is_reported() -> None:
    contract = {
        "cell": "LDO_MASTER",
        "ports": [
            {"name": "VDD", "direction": "inout", "kind": "power"},
            {"name": "VSS", "direction": "inout", "kind": "ground"},
            {"name": "VOUT", "direction": "output", "kind": "power"},
        ],
    }
    interface = canonicalize_interface(contract)
    assert interface.port_order == ("VDD", "VSS", "VOUT")
    reordered = deepcopy(contract)
    reordered["ports"] = [reordered["ports"][1], reordered["ports"][0], reordered["ports"][2]]
    structure = canonicalize_structure(reordered)
    match = match_interface(interface, structure)
    assert not match.matched
    assert "mismatch:port_order" in match.reasons


def test_case_assertions_and_template_render_are_separate_from_verdict() -> None:
    recipe = load_builtin_recipe("ldo.linear_regulator")
    contract = json.loads((product_root() / "recipes/ldo/interface_contract.json").read_text())['variants']['LDO_MASTER']
    spec = json.loads((product_root() / "recipes/ldo/behavior_spec.json").read_text())
    spec["target"] = {"module": "LDO_MASTER"}
    verification = None
    candidate = generate_linear_regulator_candidate(ModelGenerationRequest(contract=contract, spec=spec, contract_sha256="a" * 64, spec_sha256="b" * 64, generated_at="fixture", verification=verification))
    assert "module LDO_MASTER" in candidate.source
    assert candidate.test_plan["protocol"] == "AIVW_L1_EVENT v1 JSONL"
    registry = builtin_registry()
    assert any(item["name"] == "ldo.linear_regulator" for item in registry.describe()["model_classes"])
    engine = RuleTemplateEngine.for_ldo()
    context = {"interface_contract": contract, "structure_ir": contract, "ports": ["VDD", "VSS", "VOUT"], "topology": {"kind": "linear_regulator"}}
    rendered = engine.render(context)
    assert rendered.template_id == "ldo.linear_regulator"
    assert rendered.provenance["generation"] == "deterministic_rule"
    assert "verdict" not in rendered.to_dict()


def test_rule_engine_renders_master_and_aon_without_cross_topology_ports() -> None:
    recipe = load_builtin_recipe("ldo.linear_regulator")
    family = json.loads((product_root() / "recipes/ldo/interface_contract.json").read_text())
    for cell in ("LDO_MASTER", "LDO_AON"):
        contract = family["variants"][cell]
        engine = RuleTemplateEngine.for_ldo()
        context = {"interface_contract": contract, "structure_ir": contract}
        rendered = engine.render(context)
        source = rendered.source
        smoke = rendered.smoke_testbench
        if cell == "LDO_MASTER":
            assert re.search(r"module ldo_candidate \([^)]*VOUT", source)
            assert not re.search(r"\bEN\b|\ben\b", source + "\n" + smoke)
        else:
            assert re.search(r"\binput logic EN\b", source)
            assert re.search(r"\.EN\s*\(en\)", smoke)


def test_recipe_family_is_loadable_without_eda() -> None:
    recipe = load_builtin_recipe("ldo.linear_regulator")
    assert recipe.target["cell"] == "LDO_MASTER"
    assert recipe.model_class == "ldo.linear_regulator"


def test_ldo_recipe_requires_snapshot_bound_config_before_structure() -> None:
    recipe = load_builtin_recipe("ldo.linear_regulator")
    assert recipe.target["source_views"]["config"] == "config"
    gates = recipe.payload["workflow"]["gates"]
    config = next(item for item in gates if item["id"] == "config_binding")
    structure = next(item for item in gates if item["id"] == "structure")
    assert config["executor"] == "virtuoso.config_binding"
    assert config["needs"] == ["snapshot"]
    assert "config_binding" in structure["needs"]
