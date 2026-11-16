import math

import pytest

from aivworkbench.plugins.discharge_state import advance_discharge, apply_discharge, fit_discharge
from aivworkbench.waveform_metrics import Waveform


def spec():
    return {'equilibrium_v': .02, 'log_rate_knots': [[.02, math.log(1e6)], [1., math.log(1e6)]],
            'control_axis': 1, 'supply_v': 2.5, 'load_conductance_s': 1e-4}


def test_exponential_state_is_bounded_and_converges_from_either_side():
    s = spec()
    assert advance_discharge(s, 1.02, 1e-6) == pytest.approx(.02+math.exp(-1))
    assert .01 < advance_discharge(s, .01, 1e-6) < .02
    assert advance_discharge(s, 1., 1.) == .02


def test_public_analytic_decay_fit_recovers_rate():
    times = tuple(i*1e-8 for i in range(1001))
    w = Waveform(times, {'out': 'V'}, {'out': tuple(.02+math.exp(-t*1e6) for t in times)})
    fitted = fit_discharge(w, output='out', start=0., stop=1e-5, equilibrium=.02, step=1e-8, knots=17)
    assert all(math.exp(rate) == pytest.approx(1e6, rel=2e-5) for _, rate in fitted['log_rate_knots'])


def test_state_only_takes_over_after_zero_control_and_keeps_entry_value():
    m = {'discharge': spec(), 'step_s': 1e-6}
    assert apply_discharge(m, .9, 1., [2.5, 0., 1e-4], [2.5, .01, 1e-4], False) == .9
    assert apply_discharge(m, .1, .9, [2.5, 0., 1e-4], [2.5, 0., 1e-4], False) == pytest.approx(.02+.88/math.e)
    assert apply_discharge(m, 1., .9, [2.5, .01, 1e-4], [2.5, 0., 1e-4], False) == 1.
    assert apply_discharge(m, .03, .9, [2.5, 0., 1e-4], [2.5, 0., 1e-4], True) == .03
    with pytest.raises(ValueError, match='domain'):
        apply_discharge(m, .1, .9, [2.4, 0., 1e-4], [2.4, 0., 1e-4], False)


def test_nondecaying_reference_is_not_identifiable():
    w = Waveform((0., 1.), {'out': 'V'}, {'out': (1., 2.)})
    with pytest.raises(ValueError, match='monotonic'):
        fit_discharge(w, output='out', start=0., stop=1., equilibrium=0., step=.01, knots=3)


def test_python_rollout_keeps_base_state_separate_from_discharge(monkeypatch):
    from aivworkbench.plugins.sampled_state import simulate
    m = {'step_s': 1e-6, 'axes': [], 'roles': {'output': 'out'},
         'discharge': spec(), 'channels': {
             'output': {'coefficients': [1.], 'tau_s': 1e-6},
             'current': {'coefficients': [.001], 'tau_s': 0.}}}
    monkeypatch.setattr('aivworkbench.plugins.sampled_state.features', lambda *args: [1.])
    # Enable returns high at the third step; base output should still equal 1 V.
    monkeypatch.setattr('aivworkbench.plugins.sampled_state.inputs_at',
                        lambda p, c, t: [2.5, 0. if t < 3e-6 else 2.5, 1e-4])
    wave = simulate(m, {'analysis': {'stop_s': 3e-6}, 'roles': {'output': 'out'}}, {})
    assert wave.values['out'] == pytest.approx((1., .02+.98/math.e, .02+.98/math.e**2, 1.))
    assert wave.values['VPROBE:p'] == (.001,)*4
