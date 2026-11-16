from copy import deepcopy
import json
from pathlib import Path

import pytest

from aivworkbench.electrical_experiment import render_deck, validate_experiment
from aivworkbench.trajectory_planning import expand_public_plan


ROOT = Path(__file__).resolve().parents[1]/"recipes/ldo/experiment"


@pytest.mark.parametrize("cell,count", [("master", 17), ("aon", 24)])
def test_multirate_and_sequence_plans_preserve_sources(cell, count):
    original = json.loads((ROOT/("ldo_"+cell+"_dynamic_v1.json")).read_text())
    before = deepcopy(original)
    pairs = [["supply_rise", "supply_fall"], ["load_increase", "load_decrease"]]
    if cell == "aon": pairs.append(["enable_rise", "enable_fall"])
    plan = expand_public_plan(original, experiment_id="ldo_"+cell+"_dynamic_v2",
                             ramp_durations_s=[2e-6, 10e-6, 40e-6], sequential_pairs=pairs)
    assert original == before
    assert plan == json.loads((ROOT/("ldo_"+cell+"_dynamic_v2.json")).read_text())
    assert len(plan["cases"]) == count
    assert plan["analysis"]["stop_s"] == .0008
    for case in plan["cases"]:
        deck = render_deck(plan, case["id"])
        assert "tran" in deck
        if "_then_" in case["id"]:
            assert len(case["events"]) == 2
            assert case["events"][1]["before_window_s"][0] > case["events"][0]["after_window_s"][1]
            assert case["voltages"]["VSS"] == {"dc_v": 0.}


def test_discontinuous_sequence_and_invalid_rate_rejected():
    plan = json.loads((ROOT/"ldo_master_dynamic_v1.json").read_text())
    with pytest.raises(ValueError, match="discontinuous"):
        expand_public_plan(plan, experiment_id="bad", ramp_durations_s=[2e-6, 10e-6],
                           sequential_pairs=[["supply_rise", "startup"]])
    with pytest.raises(ValueError):
        expand_public_plan(plan, experiment_id="bad", ramp_durations_s=[2e-6, .001], sequential_pairs=[])
