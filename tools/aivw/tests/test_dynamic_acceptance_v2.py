"""The approved dynamic acceptance decision and its recipe binding."""

import json
from pathlib import Path

from aivworkbench.waveform_metrics import validate_waveform_policy


ROOT = Path(__file__).resolve().parents[1]


def test_approved_policy_carries_the_decided_floors():
    policy = json.loads((ROOT / "recipes/ldo/dynamic_acceptance_v2.json").read_text())
    validate_waveform_policy(policy)
    assert policy["status"] == "approved"
    assert policy["correlation"]["voltage"]["absolute"] == 0.001
    assert policy["correlation"]["current"]["absolute"] == 1e-06
    assert policy["correlation"]["time"]["absolute"] == 1e-07
    assert "user_approved_20261011" in policy["decision_source"]


def test_tip_recipes_bind_the_approved_policy():
    for cell, version in (("master", "v7"), ("aon", "v8")):
        recipe = json.loads((ROOT / f"recipes/ldo/ldo_{cell}_dynamic_model_{version}.json").read_text())
        assert recipe["inputs"]["dynamic_acceptance"] == "recipe:dynamic_acceptance_v2.json"
