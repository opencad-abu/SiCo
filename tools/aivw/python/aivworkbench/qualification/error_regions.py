"""Public trajectory diagnostics by event region, never an acceptance gate.

Counts refer to union-axis samples and therefore are not duration percentages.
Envelope crossover points are included to expose near-zero reference errors.
"""

from ..waveform_metrics import validate_waveform_policy


def _region(events, time):
    for event in events:
        if event['start_s'] <= time <= event['end_s']:
            return event['id']+':ramp'
        if event['end_s'] < time <= event['after_window_s'][1]:
            return event['id']+':tail'
    return 'baseline_or_between_events'


def diagnose_regions(candidate, golden, *, output, current, events, policy, stimuli=None):
    validate_waveform_policy(policy)
    if candidate.times[0] != golden.times[0] or candidate.times[-1] != golden.times[-1]:
        raise ValueError('diagnostic axes differ')
    result = {}
    for signal, kind in ((output, 'voltage'), (current, 'current')):
        rule = policy['correlation'][kind]
        axis = set(candidate.times) | set(golden.times)
        levels = {0.}
        if rule['relative']:
            floor = rule['absolute']/rule['relative']
            levels.update({-floor, floor})
        for a, b, x, y in zip(golden.times, golden.times[1:], golden.values[signal], golden.values[signal][1:]):
            if x != y:
                for level in levels:
                    if min(x, y) < level < max(x, y):
                        axis.add(a+(b-a)*(level-x)/(y-x))
        grouped = {}
        for t in sorted(axis):
            want, got = golden.sample(signal, t), candidate.sample(signal, t)
            limit = max(rule['absolute'], (rule['relative'] or 0.)*abs(want))
            if limit <= 0:
                raise ValueError('diagnostics require a positive comparison envelope')
            error = abs(got-want)
            ratio = error/limit
            name = _region(events, t)
            group = grouped.setdefault(name, {'samples': 0, 'outside_envelope_samples': 0,
                                               'max_absolute_error': 0., 'worst_ratio': -1.})
            group['samples'] += 1
            group['outside_envelope_samples'] += ratio > 1.
            group['max_absolute_error'] = max(group['max_absolute_error'], error)
            if ratio > group['worst_ratio']:
                group.update(worst_ratio=ratio, time_s=t, reference=want, candidate=got, limit=limit)
                if stimuli is not None:
                    group['source_inputs_at_worst'] = {name: stimuli.sample(name, t) for name in stimuli.signals
                                                        if name not in (output, current)}
        result[kind] = grouped
    return {'kind': 'public-event-region-diagnostics', 'qualification': 'NOT_ESTABLISHED',
            'acceptance_status': 'NOT_EVALUATED_BY_THIS_DIAGNOSTIC',
            'sample_counts_are_duration': False, 'regions': result}
