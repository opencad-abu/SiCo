"""Quantify reference-to-reference spread inside absolute-floor windows.

Answers one acceptance question with public data only: in the samples where
the absolute floor is the binding tolerance (reference magnitude below the
absolute/relative crossover), how far apart are the two independently
configured numerical references?  A floor far below the reference spread
cannot discriminate candidate accuracy in that window.
"""

from __future__ import annotations

from .waveform_metrics import Waveform


def _compare_on_union(reference: Waveform, crosscheck: Waveform, signal: str, rules):
    """Walk the union of both sample axes once and collect spread statistics."""

    ta, va = reference.times, reference.values[signal]
    tb, vb = crosscheck.times, crosscheck.values[signal]
    floor, relative = rules["absolute"], rules["relative"]
    crossover = floor / relative if relative else None
    max_diff = 0.0
    max_time = None
    binding_samples = 0
    binding_max = 0.0
    binding_time = None

    def value_at(times, values, pointer, t):
        # ``pointer`` marks a segment start with ``times[pointer] <= t``.
        while pointer + 1 < len(times) - 1 and times[pointer + 1] <= t:
            pointer += 1
        if times[pointer] == t or pointer + 1 >= len(times):
            return pointer, values[pointer]
        span = times[pointer + 1] - times[pointer]
        fraction = (t - times[pointer]) / span
        return pointer, values[pointer] + fraction * (values[pointer + 1] - values[pointer])

    i = j = pa = pb = 0
    while i < len(ta) or j < len(tb):
        if j >= len(tb) or (i < len(ta) and ta[i] < tb[j]):
            t = ta[i]
            i += 1
        elif i >= len(ta) or tb[j] < ta[i]:
            t = tb[j]
            j += 1
        else:
            t = ta[i]
            i += 1
            j += 1
        pa, value_a = value_at(ta, va, pa, t)
        pb, value_b = value_at(tb, vb, pb, t)
        diff = abs(value_a - value_b)
        if diff > max_diff:
            max_diff, max_time = diff, t
        if crossover is not None and abs(value_a) < crossover:
            binding_samples += 1
            if diff > binding_max:
                binding_max, binding_time = diff, t
    return {
        "samples": len(ta) + len(tb),
        "max_reference_spread": max_diff,
        "max_spread_time_s": max_time,
        "floor_binding_samples": binding_samples,
        "binding_window_spread": binding_max,
        "binding_window_spread_time_s": binding_time,
    }


def case_spread(reference: Waveform, crosscheck: Waveform, policy, *, output: str, current: str):
    return {
        "voltage": _compare_on_union(reference, crosscheck, output, policy["correlation"]["voltage"]),
        "current": _compare_on_union(reference, crosscheck, current, policy["correlation"]["current"]),
    }
