"""Physical-time waveform comparison and measured event timing.

No time shift, normalization to a candidate, or endpoint extrapolation is
allowed. Measurement definitions and acceptance thresholds are separate data.
"""

from __future__ import annotations

from bisect import bisect_left
import csv
from dataclasses import dataclass
from pathlib import Path

from .acceptance import compare_numeric, finite, validate_tolerance


@dataclass(frozen=True)
class Waveform:
    times: tuple
    signals: dict
    values: dict

    def sample(self, signal, time):
        t = finite(time, "sample time")
        if not self.times[0] <= t <= self.times[-1]:
            raise ValueError("waveform extrapolation is forbidden")
        hi = bisect_left(self.times, t)
        y = self.values[signal]
        if self.times[hi] == t:
            return y[hi]
        lo = hi - 1
        fraction = (t - self.times[lo]) / (self.times[hi] - self.times[lo])
        return y[lo] + fraction * (y[hi] - y[lo])

    def window(self, signal, start, stop):
        if not start < stop:
            raise ValueError("measurement window must increase")
        axis = [start, *[t for t in self.times if start < t < stop], stop]
        return axis, [self.sample(signal, t) for t in axis]


def read_waveform(path: Path, signals: dict, stop_s: float) -> Waveform:
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("waveform exceeds byte budget")
    with path.open(newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if header != ["time[s]", *["%s[%s]" % item for item in signals.items()]]:
            raise ValueError("waveform signal names or units differ")
        times, columns = [], {name: [] for name in signals}
        for row in reader:
            if len(row) != len(signals) + 1 or len(times) >= 250000:
                raise ValueError("waveform row or sample count invalid")
            numbers = [finite(float(s), "waveform") for s in row]
            if times and numbers[0] <= times[-1]:
                raise ValueError("waveform time must strictly increase")
            times.append(numbers[0])
            for name, number in zip(signals, numbers[1:]):
                columns[name].append(number)
    if len(times) < 2 or times[0] != 0 or abs(times[-1] - stop_s) > max(1e-15, stop_s * 1e-12):
        raise ValueError("waveform must cover the complete experiment")
    # Normalize only representational error at the prescribed final time.
    times[-1] = stop_s
    return Waveform(tuple(times), dict(signals), {k: tuple(v) for k, v in columns.items()})


def integral(axis, values):
    return sum((b - a) * (x + y) / 2 for a, b, x, y in zip(axis, axis[1:], values, values[1:]))


def window_statistics(wave, signal, window):
    axis, values = wave.window(signal, *window)
    area = integral(axis, values)
    return {"unit": wave.signals[signal], "min": min(values), "max": max(values),
            "peak_absolute": max(map(abs, values)), "mean": area / (axis[-1] - axis[0]),
            "integral": area, "integral_unit": wave.signals[signal] + "*s"}


def _crossing(axis, values, level, rising):
    for a, b, x, y in zip(axis, axis[1:], values, values[1:]):
        if (x <= level < y if rising else x >= level > y):
            return a + (b - a) * (level - x) / (y - x)
    return None


def event_measurements(wave, output, current, event, definition, reference_levels=None):
    before = window_statistics(wave, output, event["before_window_s"])["mean"]
    after = window_statistics(wave, output, event["after_window_s"])["mean"]
    observed_levels = [before, after]
    if reference_levels is not None:
        before, after = reference_levels
    delta = after - before
    start, ramp_end = event["start_s"], event["end_s"]
    stop = event["after_window_s"][1]
    axis, values = wave.window(output, start, stop)
    band = max(definition["settling_absolute_band_v"], abs(delta) * definition["settling_relative_band"])
    result = {"id": event["id"], "baseline_v": before, "final_v": after, "delta_v": delta,
              "observed_baseline_v": observed_levels[0], "observed_final_v": observed_levels[1],
              "settling_band_v": band, "timing_reference": "stimulus_ramp_midpoint",
              "output": window_statistics(wave, output, [start, stop]),
              "supply_current": window_statistics(wave, current, [start, stop])}
    if abs(delta) <= 2 * band:
        return {**result, "timing_status": "NOT_OBSERVABLE", "latency_s": None,
                "transition_10_90_s": None, "settling_after_ramp_s": None}
    crossings = [_crossing(axis, values, before + fraction * delta, delta > 0) for fraction in (.1, .5, .9)]
    tail_axis, tail_values = wave.window(output, ramp_end, stop)
    last_entry = ramp_end
    for a, b, x, y in zip(tail_axis, tail_axis[1:], tail_values, tail_values[1:]):
        if abs(y - after) > band:
            last_entry = None
        elif abs(x - after) > band:
            threshold = after + (band if x > after else -band)
            last_entry = a + (b - a) * (threshold - x) / (y - x)
    if last_entry is not None and stop - last_entry < definition["minimum_dwell_s"]:
        last_entry = None
    return {**result, "timing_status": "OBSERVED" if all(t is not None for t in crossings) else "NOT_REACHED",
            "latency_s": None if crossings[1] is None else crossings[1] - (start + ramp_end) / 2,
            "transition_10_90_s": None if any(t is None for t in crossings) else crossings[2] - crossings[0],
            "settling_after_ramp_s": None if last_entry is None else last_entry - ramp_end}


def compare_trajectories(candidate, golden, *, output, current, events, definition, policy):
    """Compare on the union of both sample axes, preserving narrow peaks."""
    validate_waveform_policy(policy)
    if candidate.times[0] != golden.times[0] or candidate.times[-1] != golden.times[-1]:
        raise ValueError("correlation axes must span the same physical interval")
    union = sorted(set(candidate.times) | set(golden.times))
    waves = {}
    for name, kind, unit in ((output, "voltage", "V"), (current, "current", "A")):
        if candidate.signals[name] != unit or golden.signals[name] != unit:
            raise ValueError("correlation signal units differ")
        rules = policy["correlation"][kind]
        # The max(abs_floor, rel*abs(reference)) envelope has kinks at its
        # crossover values. Endpoint-only checks can miss an error near zero
        # even when both piecewise-linear waveforms pass at all native knots.
        axis = set(union)
        levels = {0.}
        if rules["relative"]:
            crossover = rules["absolute"]/rules["relative"]
            levels.update({-crossover, crossover})
        for a, b, x, y in zip(golden.times, golden.times[1:], golden.values[name], golden.values[name][1:]):
            if x != y:
                for level in levels:
                    if min(x, y) < level < max(x, y):
                        axis.add(a+(b-a)*(level-x)/(y-x))
        axis = sorted(axis)
        results = [compare_numeric(candidate.sample(name, t), golden.sample(name, t), unit=unit,
                                   tolerance=rules) for t in axis]
        worst = max(range(len(axis)), key=lambda i: results[i]["absolute_error"])
        waves[kind] = {"diagnostic_status": "PASS" if all(r["status"] == "PASS" for r in results) else "FAIL_TOLERANCE",
                       "samples": len(axis), "failed_samples": sum(r["status"] != "PASS" for r in results),
                       "max_absolute_error": results[worst]["absolute_error"], "worst_time_s": axis[worst],
                       "unit": unit}
    timing = []
    for event in events:
        reference = event_measurements(golden, output, current, event, definition)
        actual = event_measurements(candidate, output, current, event, definition,
                                    [reference["baseline_v"], reference["final_v"]])
        metrics = {}
        for metric in ("latency_s", "transition_10_90_s", "settling_after_ramp_s"):
            if reference[metric] is None:
                metrics[metric] = {"status": "NOT_OBSERVABLE" if reference["timing_status"] == "NOT_OBSERVABLE" else "BLOCKED_EVIDENCE"}
            elif actual[metric] is None:
                metrics[metric] = {"status": "FAIL_NOT_REACHED"}
            else:
                metrics[metric] = compare_numeric(actual[metric], reference[metric], unit="s", tolerance=policy["correlation"]["time"])
        current_metrics = {metric: compare_numeric(actual["supply_current"][metric], reference["supply_current"][metric],
                                                   unit="A", tolerance=policy["correlation"]["current"])
                           for metric in ("peak_absolute", "mean")}
        timing.append({"id": event["id"], "reference": reference, "actual": actual, "comparisons": metrics,
                       "current_comparisons": current_metrics})
    passed = all(v["diagnostic_status"] == "PASS" for v in waves.values()) and all(
        m["status"] in {"PASS", "NOT_OBSERVABLE"} for e in timing for m in
        [*e["comparisons"].values(), *e["current_comparisons"].values()])
    return {"status": ("PASS" if passed else "FAIL_CORRELATION") if policy["status"] == "approved" else "BLOCKED_CONTRACT",
            "diagnostic_status": "PASS" if passed else "FAIL_CORRELATION", "waveforms": waves,
            "events": timing, "alignment": "physical_time_union_no_shift", "product_status": "NOT_EVALUATED"}


def validate_waveform_policy(value):
    if set(value) != {"schema_version", "kind", "status", "decision_source", "correlation", "product"}:
        raise ValueError("waveform policy fields differ")
    if value["schema_version"] != 1 or value["kind"] != "physical-time-correlation" or value["status"] not in {"proposed", "approved"}:
        raise ValueError("unsupported waveform acceptance policy")
    if not isinstance(value["decision_source"], str) or not value["decision_source"]:
        raise ValueError("waveform decision provenance required")
    if set(value["correlation"]) != {"voltage", "current", "time"}:
        raise ValueError("waveform correlation metrics are incomplete")
    for kind, unit in (("voltage", "V"), ("current", "A"), ("time", "s")):
        rule = validate_tolerance(value["correlation"][kind])
        if rule["unit"] != unit or rule["absolute"] is None:
            raise ValueError("dynamic metrics require unit-bearing absolute tolerances")
    if value["product"] != {"current_limit_a": None, "startup_limit_s": None, "settling_limit_s": None}:
        raise ValueError("product current/time acceptance needs a separate decision version")
    return value
