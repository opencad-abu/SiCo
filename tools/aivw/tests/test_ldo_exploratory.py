from __future__ import annotations

import json
from pathlib import Path

import pytest

from aivworkbench.ldo_exploratory import (
    LDOExplorationError,
    build_exploratory_plan,
    read_exploratory_policy,
)
from aivworkbench.executors.spectre_exploratory import build_exploratory_deck


ROOT = Path(__file__).resolve().parents[1]
POLICY_DIR = ROOT / "recipes" / "ldo" / "exploration"


@pytest.mark.parametrize(
    ("name", "topology", "count"),
    (("ldo_master_exploratory.json", "LDO_MASTER", 10), ("ldo_aon_exploratory.json", "LDO_AON", 18)),
)
def test_source_derived_plans_are_bounded_and_pending(name: str, topology: str, count: int) -> None:
    policy = read_exploratory_policy(POLICY_DIR / name)
    plan = build_exploratory_plan(policy)

    assert plan["topology"] == topology
    assert len(plan["cases"]) == count
    assert plan["holdout"] == {
        "locked": True,
        "public_values_included": False,
        "partition_policy": "hidden_holdout_excluded_from_exploration_plan",
    }
    assert plan["tolerance_contract"]["status"] == "BLOCKED_CONTRACT"
    assert plan["tolerance_contract"]["metrics"] == {}
    assert plan["behavior_verdict"] == "NOT_ESTABLISHED"
    assert plan["correlation_status"] == "BLOCKED_CONTRACT"
    assert plan["plan_digest"].startswith("sha256:")
    assert all(case["dataset"] if "dataset" in case else True for case in plan["cases"])


def test_master_plan_has_no_enable_cases() -> None:
    policy = read_exploratory_policy(POLICY_DIR / "ldo_master_exploratory.json")
    plan = build_exploratory_plan(policy)
    assert not any(case["purpose"] == "enable_voltage_sweep" for case in plan["cases"])


def test_exploratory_deck_uses_source_ports_and_payload_relative_includes() -> None:
    deck = build_exploratory_deck(
        "subckt LDO_MASTER VDD VOUT VSS\nends LDO_MASTER\n",
        "LDO_MASTER",
        {"kind": "dc", "stimulus": {"vdd_v": 5.0, "load_resistance_ohm": 10000.0}},
        "gpdk045.scs",
        "tt",
    )
    assert 'include "../../models/gpdk045.scs" section=tt' in deck
    assert 'include "../../circuit.scs"' in deck
    assert "XUUT (VDD VOUT VSS) LDO_MASTER" in deck
    assert "XUUT (EN VDD VOUT VSS)" not in deck


def test_master_deck_rejects_enable_and_bad_load() -> None:
    with pytest.raises(ValueError):
        build_exploratory_deck(
            "subckt LDO_MASTER VDD VOUT VSS\nends LDO_MASTER\n",
            "LDO_MASTER",
            {"kind": "dc", "stimulus": {"vdd_v": 5.0, "en_v": 1.0, "load_resistance_ohm": 10000.0}},
            "gpdk045.scs",
            "tt",
        )
    with pytest.raises(ValueError, match="cannot contain EN"):
        build_exploratory_deck(
            "subckt LDO_MASTER VDD VOUT VSS\nends LDO_MASTER\n",
            "LDO_MASTER",
            {"kind": "transient", "stimulus": {"initial_vdd_v": 0.0, "final_vdd_v": 5.0,
                                                   "initial_en_v": 0.0, "load_resistance_ohm": 10000.0}},
            "gpdk045.scs",
            "tt",
        )


def test_exploratory_deck_rejects_illegal_case_kind_and_topology() -> None:
    case = {"kind": "ac", "stimulus": {"vdd_v": 5.0, "load_resistance_ohm": 10000.0}}
    with pytest.raises(ValueError, match="case kind"):
        build_exploratory_deck("subckt LDO_MASTER VDD VOUT VSS\nends LDO_MASTER\n", "LDO_MASTER", case, "gpdk045.scs", "tt")
    with pytest.raises(ValueError, match="topology"):
        build_exploratory_deck("subckt LDO_MASTER VDD VOUT VSS\nends LDO_MASTER\n", "OTHER", {"kind": "dc", "stimulus": {"vdd_v": 5.0, "load_resistance_ohm": 10000.0}}, "gpdk045.scs", "tt")


def test_aon_plan_contains_enable_voltage_and_edges() -> None:
    policy = read_exploratory_policy(POLICY_DIR / "ldo_aon_exploratory.json")
    plan = build_exploratory_plan(policy)
    purposes = [case["purpose"] for case in plan["cases"]]
    assert "enable_voltage_sweep" in purposes
    assert "enable_rise" in purposes
    assert "enable_fall" in purposes
    enable_values = [case["stimulus"]["en_v"] for case in plan["cases"] if case["purpose"] == "enable_voltage_sweep"]
    assert enable_values == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]


@pytest.mark.parametrize(
    ("change", "message"),
    (
        (lambda p: p.update(extra=True), "fields"),
        (lambda p: p["topologies"].update(LDO_AON={}), "exactly one topology"),
        (lambda p: p["topologies"]["LDO_MASTER"]["en_voltage_v"].update(nominal=1.0), "cannot declare EN"),
        (lambda p: p["tolerance_contract"].update(metrics={"vout": 0.01}), "acceptance tolerances"),
        (lambda p: p["matrix"]["enable_voltage_sweep"] if False else p["matrix"].update(enable_voltage_sweep=True), "cannot enable EN"),
    ),
)
def test_policy_rejects_unsafe_mutations(tmp_path: Path, change, message: str) -> None:
    source = json.loads((POLICY_DIR / "ldo_master_exploratory.json").read_text())
    change(source)
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(source))
    with pytest.raises(LDOExplorationError, match=message):
        read_exploratory_policy(path)
