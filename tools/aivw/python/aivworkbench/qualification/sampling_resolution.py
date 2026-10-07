"""Reconstruction diagnostics for PUBLIC reference waveform sampling.

Exact reference samples are an oracle diagnostic, never a model candidate or a
mathematical lower bound on all possible interpolants. Preserve physical time.
"""

import math

from ..waveform_metrics import Waveform, compare_trajectories


def reconstruct_window(reference, *, signals, start, stop, step):
    if not all(math.isfinite(v) for v in (start, stop, step)) or not reference.times[0] <= start < stop <= reference.times[-1] or step <= 0:
        raise ValueError('invalid physical sampling window')
    if (stop-start)/step > 200000:
        raise ValueError('sampling window exceeds diagnostic budget')
    # Align samples to the global t=0 grid, not the stimulus edge. Include only
    # the bounding grid points so comparison never extrapolates a reconstruction.
    lo = math.floor(start/step)
    hi = math.ceil(stop/step)
    # Division can round to an integer whose product lies one ulp inside
    # the window. Keep the global grid and extend it, never shift time.
    if lo*step > start:
        lo -= 1
    if hi*step < stop:
        hi += 1
    grid = [i*step for i in range(lo, hi+1)]
    if grid[0] < reference.times[0] or grid[-1] > reference.times[-1]:
        raise ValueError('sampling grid extends outside available reference')
    sampled = Waveform(tuple(grid), {s: reference.signals[s] for s in signals},
                       {s: tuple(reference.sample(s, t) for t in grid) for s in signals})
    truth_times = (start, *[t for t in reference.times if start < t < stop], stop)
    reconstruction_times = (start, *[t for t in grid if start < t < stop], stop)
    truth = Waveform(tuple(truth_times), sampled.signals,
                     {s: tuple(reference.sample(s, t) for t in truth_times) for s in signals})
    actual = Waveform(tuple(reconstruction_times), sampled.signals,
                      {s: tuple(sampled.sample(s, t) for t in reconstruction_times) for s in signals})
    return actual, truth


def assess_resolution(reference, *, output, current, start, stop, steps, policy, definition):
    if not steps or len(steps) > 16 or len(set(steps)) != len(steps):
        raise ValueError('distinct bounded sampling steps required')
    rows = []
    for step in steps:
        actual, truth = reconstruct_window(reference, signals=[output, current], start=start, stop=stop, step=step)
        result = compare_trajectories(actual, truth, output=output, current=current, events=[],
                                      definition=definition, policy=policy)
        rows.append({'step_s': step, 'sample_count': len(actual.times), 'waveforms': result['waveforms'],
                     'diagnostic_status': result['diagnostic_status']})
    return {'kind': 'public-reference-sampling-diagnostic', 'window_s': [start, stop], 'steps': rows,
            'qualification': 'NOT_ESTABLISHED', 'candidate_generated': False,
            'timing_metrics_evaluated': False, 'holdout_evaluated': False,
            'interpretation': 'exact_reference_sample_reconstruction_not_model_or_universal_error_bound'}
