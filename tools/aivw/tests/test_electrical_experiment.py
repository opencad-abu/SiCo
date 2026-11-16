from copy import deepcopy
from pathlib import Path

import pytest

from aivworkbench.electrical_experiment import read_experiment, validate_experiment, render_deck, extract_subcircuits
from aivworkbench.recipe import load_builtin_recipe


ROOT=Path(__file__).resolve().parents[1]/"recipes"/"ldo"/"experiment"


def plan(name="aon"):
    return read_experiment(ROOT/("ldo_%s_characterization_v2.json" % name))


def test_nominal_supply_matches_user_intent_and_is_recipe_data():
    p=plan(); assert p["acceptance"]["nominal_supply_v"] == 2.5
    for c in p["cases"]:
        if c["product_check"]: assert c["voltages"]["VDD"] == {"dc_v":2.5}
    for name in ("master","aon"):
        r=load_builtin_recipe("ldo.%s_characterization_v2" % name)
        assert "characterization_plan" in r.resolved_inputs


def test_explicit_enable_in_startup_and_source_order():
    p=plan(); deck=render_deck(p,"startup")
    assert "VS_EN (EN 0) vsource dc=2.5" in deck
    assert "XUUT (EN VDD VOUT VSS) LDO_AON" in deck
    assert "temp=27" in deck
    assert "0.00011" in deck
    assert "EN" not in render_deck(plan("master"),"startup")


def test_renderer_has_no_dependency_on_ldo_names():
    p=plan("master")
    rename={"VDD":"VP","VSS":"VN","VOUT":"OUTPUT"}
    p["target"]={"library":"different_library","cell":"buffer_block","term_order":[rename[n] for n in p["target"]["term_order"]]}
    for k in ("supply","reference","output"):p["roles"][k]=rename[p["roles"][k]]
    for c in p["cases"]:c["voltages"]={rename[n]:v for n,v in c["voltages"].items()}
    d=render_deck(p,"startup")
    assert "XUUT (VP OUTPUT VN) buffer_block" in d
    assert "LDO" not in d and "VDD" not in d


@pytest.mark.parametrize("mutation", [
    lambda p:p["cases"][0]["voltages"].pop("EN"),
    lambda p:p["cases"][0]["voltages"].update(EXTRA={"dc_v":0.}),
    lambda p:p["cases"][0].update(load_ohm=-1),
    lambda p:p["cases"][0]["voltages"]["VDD"].update(dc_v=float('nan')),
    lambda p:p["cases"][0]["voltages"]["VDD"].update(dc_v=2.4),
    lambda p:p["cases"][-1]["voltages"]["VDD"]["pwl"].__setitem__(0,[0.001,0.]),
    lambda p:p["target"].update(cell='foo\ninclude "evil"'),
    lambda p:p["analysis"].update(sample_times_s=[0.004,0.003]),
    lambda p:p["cases"].append(deepcopy(p["cases"][0])),
])
def test_invalid_stimulus_cannot_reach_eda(mutation):
    p=plan();mutation(p)
    with pytest.raises(ValueError):validate_experiment(p)


def test_source_subcircuit_order_must_match():
    p=plan("master")
    text="subckt LDO_MASTER VDD VOUT VSS\nR (VOUT VSS) resistor r=1\nends LDO_MASTER\nVTOP (VDD 0) vsource dc=5\n"
    result=extract_subcircuits(text,p["target"])
    assert "VTOP" not in result
    with pytest.raises(ValueError):extract_subcircuits(text.replace("VDD VOUT VSS","VOUT VDD VSS"),p["target"])
