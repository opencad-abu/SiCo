"""Analytic checks for decaying input memory and independent base state."""

import math

import pytest

from aivworkbench.plugins.state_memory import advance_memory, base_samples, fit_memory, validate_constants
from aivworkbench.plugins.sampled_state import simulate


def model():
    return {"step_s": 1., "axes": [{"knots": [0., 1.], "slew_scale_per_s": 1.}],
            "memory": {"time_constants_s": [0., 2.], "coefficients": {
                "output": [0.]*12+[1.]+[0.]*11, "current": [0.]*24}},
            "roles": {"supply": "VDD", "controls": [], "output": "OUT"},
            "identification_options": {"slew_features": "global"},
            "channels": {"output": {"coefficients": [1., 0., 0.], "tau_s": 2.},
                         "current": {"coefficients": [0., 0., 0.], "tau_s": 0.}}}


def test_memory_retains_tail_and_initialization_clears_it():
    m = model()
    state = [99.]*24
    advance_memory(m, state, [0.], [0.], initialize=True)
    assert state == [0.]*24
    a = math.exp(-.5)
    advance_memory(m, state, [1.], [0.])
    assert state[12] == pytest.approx(1-a)
    advance_memory(m, state, [1.], [1.])
    assert state[0] == 0.
    assert state[12] == pytest.approx(a*(1-a))
    # Falling edge excites a separate bank and retains the rising tail.
    advance_memory(m, state, [0.], [1.])
    assert state[12] == pytest.approx(a*a*(1-a))
    assert state[18] == pytest.approx(1-a)
    advance_memory(m, state, [0.], [0.], initialize=True)
    assert state == [0.]*24


@pytest.mark.parametrize("taus", [[], [0.], [0., 0.], [0., -1.], [0., float('nan')], [0., .5], [0., 30.]])
def test_invalid_memory_poles_rejected(taus):
    with pytest.raises(ValueError):
        validate_constants(taus, 1., 100.)


def test_correction_does_not_feed_back_into_base(monkeypatch):
    m = model()
    # One analytic input coordinate bypasses the electrical stimulus decoder.
    monkeypatch.setattr('aivworkbench.plugins.sampled_state.inputs_at', lambda p, c, t: [min(1., t)])
    wave = simulate(m, {"analysis": {"stop_s": 3.}, "roles": {"output": "OUT"}}, {})
    a = math.exp(-.5)
    assert wave.values['OUT'] == pytest.approx([1., 1.+(1-a), 1.+a*(1-a), 1.+a*a*(1-a)])


def test_residual_fit_recovers_synthetic_decay_without_teacher_forcing():
    from copy import deepcopy
    m = model()
    m['step_s'] = .001
    m['axes'].append(deepcopy(m['axes'][0]))
    m['memory']['time_constants_s'] = [0., .002]
    m['memory']['coefficients'] = {'output': [0.]*24+[.2]+[0.]*23,
                                   'current': [0.]*24+[.0002]+[0.]*23}
    m['identification_options']['memory_time_constants_s'] = [0., .002]
    for channel in m['channels'].values():
        channel['coefficients'] += [0., 0.]
        channel['tau_s'] = 0.
    case = {'id': 'public', 'voltages': {'VDD': {'pwl': [[0., 0.], [.01, 0.], [.03, 1.], [.1, 1.]]}},
            'load_conductance_pwl_s': None, 'load_ohm': 1.,
            'events': [{'start_s': .01, 'end_s': .03}]}
    plan = {'roles': m['roles'], 'analysis': {'stop_s': .1}, 'cases': [case]}
    golden = simulate(m, plan, case)
    m.pop('memory')
    base = simulate(m, plan, case)
    indices = [0, 9, 10, 11, 29, 30, 31, 100]
    samples = base_samples(m, plan, case, indices)
    for i in indices:
        assert samples[i] == {'output': base.values['OUT'][i], 'current': base.values['VPROBE:p'][i]}
    fit_memory(m, plan, {'public': golden})
    actual = simulate(m, plan, case)
    assert max(abs(a-b) for a, b in zip(actual.values['OUT'], golden.values['OUT'])) < 1e-4
    assert max(abs(a-b) for a, b in zip(actual.values['VPROBE:p'], golden.values['VPROBE:p'])) < 1e-7
