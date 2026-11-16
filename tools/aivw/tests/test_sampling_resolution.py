import pytest

from aivworkbench.qualification.sampling_resolution import reconstruct_window, assess_resolution
from aivworkbench.waveform_metrics import Waveform
from test_waveform_metrics import policy, DEFINITION


def test_global_grid_and_no_phase_shift_at_event_window():
    w = Waveform((0., .45, .5, .55, 1.), {'out': 'V', 'i': 'A'},
                 {'out': (0., 0., 1., 0., 0.), 'i': (.001,)*5})
    actual, truth = reconstruct_window(w, signals=['out', 'i'], start=.41, stop=.61, step=.2)
    assert actual.times == (.41, .6000000000000001, .61)
    assert .5 in truth.times and .5 not in actual.times
    result = assess_resolution(w, output='out', current='i', start=.2, stop=.8, steps=[.2, .05], policy=policy(), definition=DEFINITION)
    assert result['steps'][0]['diagnostic_status'] == 'FAIL_CORRELATION'
    assert result['steps'][1]['diagnostic_status'] == 'PASS'
    assert result['candidate_generated'] is False


def test_resolution_budget_and_extrapolation_rejected():
    w = Waveform((0., 1.), {'out': 'V'}, {'out': (0., 1.)})
    for start, stop, step in [(0., 1., 1e-9), (.1, 1., .3), (.2, .1, .01), (0., 1., float('nan'))]:
        with pytest.raises(ValueError):
            reconstruct_window(w, signals=['out'], start=start, stop=stop, step=step)


def test_rounded_grid_product_still_brackets_window():
    w = Waveform((0., 1.), {'out': 'V'}, {'out': (0., 1.)})
    start = .000499 - .000001
    actual, truth = reconstruct_window(w, signals=['out'], start=start,
                                      stop=.00056, step=1e-9)
    assert actual.times[0] == truth.times[0] == start
    assert actual.values['out'] == pytest.approx(actual.times)
