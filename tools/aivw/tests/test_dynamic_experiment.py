from copy import deepcopy
from pathlib import Path

import pytest

from aivworkbench.electrical_experiment import read_experiment, render_deck, validate_experiment
from aivworkbench.recipe import load_builtin_recipe


ROOT = Path(__file__).resolve().parents[1]/"recipes/ldo/experiment"


def plan():
    return read_experiment(ROOT/"ldo_aon_dynamic_v1.json")


def test_dynamic_load_is_a_conductance_stimulus_without_new_source_ports():
    p = plan()
    deck = render_deck(p, "load_increase")
    assert "BLOAD (VOUT VSS) bsource i=v(VOUT,VSS)*v(aivw_internal_load_g)" in deck
    assert "XUUT (EN VDD VOUT VSS) LDO_AON" in deck
    assert "RLOAD" not in deck
    for name in ("master", "aon"):
        assert load_builtin_recipe("ldo."+name+"_dynamic_v1").input_path("characterization_plan").is_file()


@pytest.mark.parametrize("mutate", [
    lambda p: p["cases"][3]["load_conductance_pwl_s"][0].__setitem__(1, 0.),
    lambda p: p["cases"][3].update(load_ohm=10000.),
    lambda p: p["cases"][0]["events"][0].update(end_s=.00012),
    lambda p: p["cases"][0].update(events=[]),
    lambda p: p["cases"][0]["events"].append(deepcopy(p["cases"][0]["events"][0])),
    lambda p: p["cases"][0]["events"][0].update(after_window_s=[0., .00001]),
    lambda p: p["measurement"].update(sample_step_s=1e-12),
    lambda p: p["cases"][0].update(product_check=True),
])
def test_invalid_dynamic_contract_cannot_reach_simulation(mutate):
    p = plan()
    mutate(p)
    with pytest.raises(ValueError):
        validate_experiment(p)


def test_static_contract_remains_version_one():
    p = read_experiment(ROOT/"ldo_master_characterization_v2.json")
    assert p["schema_version"] == 1
    assert "BLOAD" not in render_deck(p, "startup")
