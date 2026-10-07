"""Piecewise rational transfer crossings and continuous complex path phase."""

import cmath
import math
from bisect import bisect_left

from .ac_measure import _unit
from .result_contract import finite
from .waveform_measure import MeasurementIssue, convert
from .waveform_schema import MAX_SAMPLES


def issue(reason):
    raise MeasurementIssue("error", reason)


def frequency(quantity):
    _unit(quantity["unit"], {"Hz"}, "frequency_unit_incompatible")
    value = convert(quantity["value"], quantity["unit"], "Hz")
    if value < 0:
        issue("negative_frequency")
    return value


def axis(wave, role):
    if wave["status"] != "waveform" or not wave["samples"]:
        reason = "source_" + wave["status"] if wave["status"] != "waveform" else "empty_waveform"
        raise MeasurementIssue("missing", role + ":" + reason)
    _unit(wave["x_unit"], {"Hz"}, "frequency_axis_unit_incompatible")
    unit = _unit(wave["y_unit"], {"V", "A", "dimensionless"}, "phasor_unit_incompatible")
    xs, zs = [], []
    for row in wave["samples"]:
        x = convert(row[0], wave["x_unit"], "Hz")
        if xs and x <= xs[-1]:
            issue("frequency_precision_loss")
        xs.append(x)
        zs.append(complex(*(convert(v, wave["y_unit"], unit) for v in row[1:])))
    return xs, zs, unit


def interpolate(xs, zs, x):
    i = bisect_left(xs, x)
    if xs[i] == x:
        return zs[i]
    fraction = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
    return (1 - fraction) * zs[i - 1] + fraction * zs[i]


def grid(first, second, window, floor):
    lo, hi = (frequency(window[k]) for k in ("start", "stop"))
    if lo >= hi:
        issue("invalid_window")
    ax, az, unit = axis(first, "input")
    bx, bz, other = axis(second, "output")
    if unit != other:
        issue("transfer_unit_incompatible")
    if lo < max(ax[0], bx[0]) or hi > min(ax[-1], bx[-1]):
        raise MeasurementIssue("missing", "window_out_of_range")
    xs = sorted({lo, hi, *(x for x in ax if lo < x < hi), *(x for x in bx if lo < x < hi)})
    if len(xs) > MAX_SAMPLES:
        issue("union_grid_limit")
    za = [interpolate(ax, az, x) for x in xs]
    zb = [interpolate(bx, bz, x) for x in xs]
    limit = convert(floor["value"], floor["unit"], unit)
    for a, b in zip(za, za[1:]):
        scale = max(abs(a), abs(b))
        if not finite(scale):
            issue("numeric_overflow")
        if scale == 0:
            issue("denominator_at_or_below_floor_in_window")
        aa, delta = a / scale, b / scale - a / scale
        t = (
            max(0, min(1, -(aa.real * delta.real + aa.imag * delta.imag) / abs(delta) ** 2))
            if delta
            else 0
        )
        if abs((1 - t) * a + t * b) <= limit:
            issue("denominator_at_or_below_floor_in_window")
    return xs, za, zb, dict(value=limit, unit=unit)


def polynomial(a, b, c, d, target):
    """|c+(d-c)t|² - target² |a+(b-a)t|², scaled before squaring."""
    scale = max(abs(v) for z in (a, b, c, d) for v in (z.real, z.imag))
    if not scale or not finite(scale):
        issue("numeric_overflow")
    scale = math.ldexp(1.0, math.frexp(scale)[1] - 1)
    a, b, c, d = a / scale * target, b / scale * target, c / scale, d / scale
    scale = max(abs(v) for z in (a, b, c, d) for v in (z.real, z.imag))
    scale = math.ldexp(1.0, math.frexp(scale)[1] - 1)
    a, b, c, d = a / scale, b / scale, c / scale, d / scale
    da, dc = b - a, d - c
    aa = dc.real**2 + dc.imag**2 - da.real**2 - da.imag**2
    bb = 2 * ((c.conjugate() * dc).real - (a.conjugate() * da).real)
    cc = c.real**2 + c.imag**2 - a.real**2 - a.imag**2
    return aa, bb, cc


def roots(coeff):
    a, b, c = coeff
    if a == 0:
        return [-c / b] if b else []
    disc = b * b - 4 * a * c
    if disc and abs(disc) <= 16 * math.ulp(max(b * b, abs(4 * a * c))):
        issue("crossing_numerically_ambiguous")
    if disc <= 0:
        return []  # Interior tangency does not cross the level.
    q = -0.5 * (b + math.copysign(math.sqrt(disc), b))
    return sorted([q / a, c / q]) if q else [0]


def crossings(xs, za, zb, target):
    events, pending = [], None
    for i in range(len(xs) - 1):
        coeff = polynomial(za[i], za[i + 1], zb[i], zb[i + 1], target)
        if coeff == (0, 0, 0):
            issue("threshold_plateau")
        # Classify intervals separated by exact quadratic roots, including a knot root.
        ts = [0] + [t for t in roots(coeff) if 0 < t < 1] + [1]
        signs = []
        for left, right in zip(ts, ts[1:]):
            mid = (left + right) / 2
            val = (coeff[0] * mid + coeff[1]) * mid + coeff[2]
            if val == 0:
                # A tangent may lie exactly at the midpoint without changing sides.
                mid = left + (right - left) / 3
                val = (coeff[0] * mid + coeff[1]) * mid + coeff[2]
            signs.append(1 if val > 0 else -1 if val < 0 else 0)
        if 0 in signs:
            issue("crossing_numerically_ambiguous")
        if pending is not None and pending != signs[0]:
            events.append(
                dict(
                    frequency_hz=xs[i],
                    direction="rising" if signs[0] > 0 else "falling",
                    bracket_hz=[xs[i - 1], xs[i + 1]],
                )
            )
        for j, t in enumerate(ts[1:-1]):
            if signs[j] != signs[j + 1]:
                events.append(
                    dict(
                        frequency_hz=(1 - t) * xs[i] + t * xs[i + 1],
                        direction="rising" if signs[j + 1] > 0 else "falling",
                        bracket_hz=[xs[i], xs[i + 1]],
                    )
                )
        pending = signs[-1]
        if len(events) > 256:
            issue("crossing_count_limit")
    return events


def phase_at(xs, za, zb, selected, sign, turns, floor):
    stop = selected["frequency_hz"]
    last = bisect_left(xs, stop)
    px = xs[:last] + [stop]
    pa = za[:last] + [interpolate(xs, za, stop)]
    pb = zb[:last] + [interpolate(xs, zb, stop)]
    initial = cmath.phase(sign * pb[0] / pa[0])
    if initial <= -math.pi:
        initial = math.pi
    initial += 2 * math.pi * turns
    phase = initial
    for a, b, c, d in zip(pa, pa[1:], pb, pb[1:]):
        coeff = polynomial(a, b, c, d, floor)
        candidates = [coeff[2], sum(coeff)]
        if coeff[0] > 0:
            vertex = -coeff[1] / (2 * coeff[0])
            if 0 < vertex < 1:
                candidates.append((coeff[0] * vertex + coeff[1]) * vertex + coeff[2])
        if min(candidates) <= 0:
            issue("phase_magnitude_at_or_below_floor_in_path")
        # Each nonzero Cartesian line segment turns <pi; their ratio may turn >pi.
        phase += cmath.phase(d / abs(d) / (c / abs(c))) - cmath.phase(b / abs(b) / (a / abs(a)))
    if not finite(phase):
        issue("numeric_overflow")
    return phase, dict(
        anchor_frequency_hz=px[0],
        anchor_phase_rad=initial,
        crossing_phase_rad=phase,
        phase_anchor_turns=turns,
        continuation="numerator_phase_change_minus_denominator_phase_change",
    )
