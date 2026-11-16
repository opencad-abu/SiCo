from copy import deepcopy

import pytest

from aivworkbench.plugins.measured_dc import predict_surface, render_surface, render_dc_testbench
from aivworkbench.workspace import stable_digest


def surface():
    return {"module":"other_block","ports":["P","O","N"],"roles":{"supply":"P","output":"O","reference":"N","controls":[]},
            "supply_knots":[[1.,0.4],[2.,0.8],[3.,1.3]],"conductance_delta_knots":[[0.0001,0.1],[0.0002,0.],[0.0004,-0.1]],
            "reference_load_ohm":5000.,"load_range_ohm":[2500.,10000.],"fixed_controls_v":{}}


def test_measured_surface_uses_knots_and_load_with_no_extrapolation():
    model=surface()
    assert predict_surface(model,supply_v=2.,load_ohm=5000.)==pytest.approx(.8)
    assert predict_surface(model,supply_v=2.5,load_ohm=10000.)==pytest.approx(1.15)
    with pytest.raises(ValueError):predict_surface(model,supply_v=4.,load_ohm=5000.)
    with pytest.raises(ValueError):predict_surface(model,supply_v=2.,load_ohm=100.)


def test_renderer_preserves_target_interface_and_bounds():
    text=render_surface(surface())
    assert "other_block" in text and "LDO" not in text
    assert "OUTSIDE_MEASURED_DC_DOMAIN" in text
    assert "input real P" in text and "output var real O" in text
    assert "LOAD_OHM" in text


def test_tb_has_independent_model_instances_for_resistive_loads():
    cases=[{"id":"case_a","load_ohm":5000.,"voltages":{"P":{"dc_v":2.},"N":{"dc_v":0.}}},
           {"id":"case_b","load_ohm":10000.,"voltages":{"P":{"dc_v":2.5},"N":{"dc_v":0.}}}]
    text=render_dc_testbench(surface(),cases)
    assert "dut_000" in text and "dut_001" in text
    assert "LOAD_OHM(10000)" in text and "LOAD_OHM(5000)" in text
