from aivworkbench.qualification.error_regions import diagnose_regions
from aivworkbench.waveform_metrics import Waveform
from test_waveform_metrics import policy, EVENT


def test_regions_distinguish_ramp_and_tail_without_realigning():
    golden = Waveform((0., 1., 2., 3., 4.), {'out': 'V', 'i': 'A'},
                      {'out': (0., 0., 1., 1., 1.), 'i': (.001,)*5})
    actual = Waveform(golden.times, golden.signals,
                      {'out': (0., .1, 1., 1.2, 1.), 'i': (.001,)*5})
    r = diagnose_regions(actual, golden, output='out', current='i', events=[EVENT], policy=policy())
    v = r['regions']['voltage']
    assert v['step:ramp']['outside_envelope_samples'] > 0
    assert v['step:tail']['time_s'] == 3.
    assert v['step:tail']['worst_ratio'] > 3.9
    assert r['qualification'] == 'NOT_ESTABLISHED'
    assert not r['sample_counts_are_duration']


def test_near_zero_interior_envelope_crossovers_are_included():
    golden = Waveform((0., 1.), {'out': 'V', 'i': 'A'}, {'out': (-1., 1.), 'i': (.001, .001)})
    actual = Waveform(golden.times, golden.signals, {'out': (-.98, 1.02), 'i': (.001, .001)})
    r = diagnose_regions(actual, golden, output='out', current='i', events=[], policy=policy())
    region = r['regions']['voltage']['baseline_or_between_events']
    assert region['samples'] == 5
    assert region['outside_envelope_samples'] == 3
    assert region['worst_ratio'] > 19.9
