from copy import deepcopy

import pytest

from aivworkbench.waveform_metrics import Waveform, compare_trajectories, event_measurements, read_waveform


DEFINITION = {"settling_relative_band": .02, "settling_absolute_band_v": .001,
              "minimum_dwell_s": .2, "sample_step_s": .1}
EVENT = {"id": "step", "signal": "input", "start_s": 1., "end_s": 2.,
         "before_window_s": [0., .5], "after_window_s": [3., 4.]}


def wave(axis, values):
    return Waveform(tuple(axis), {"out": "V", "supply": "A"},
                    {"out": tuple(values), "supply": tuple(.001 for _ in axis)})


def policy():
    return {"schema_version": 1, "kind": "physical-time-correlation", "status": "proposed",
            "decision_source": "synthetic_test", "correlation": {
                k: {"unit": u, "relative": .05, "absolute": a, "normalization": "reference_magnitude"}
                for k, u, a in (("voltage", "V", .001), ("current", "A", 1e-6), ("time", "s", .1))},
            "product": {"current_limit_a": None, "startup_limit_s": None, "settling_limit_s": None}}


def compare(a, b, p=None):
    return compare_trajectories(a, b, output="out", current="supply", events=[],
                                definition=DEFINITION, policy=p or policy())


def test_pending_acceptance_cannot_produce_formal_pass():
    a = wave([0., 1.], [0., 1.])
    r = compare(a, a)
    assert r["status"] == "BLOCKED_CONTRACT" and r["diagnostic_status"] == "PASS"
    p = policy(); p["status"] = "approved"
    assert compare(a, a, p)["status"] == "PASS"


def test_union_axis_detects_a_peak_absent_from_candidate_sampling():
    actual = wave([0., 1.], [0., 0.])
    reference = wave([0., .49, .5, .51, 1.], [0., 0., 1., 0., 0.])
    r = compare(actual, reference)
    assert r["waveforms"]["voltage"]["max_absolute_error"] == 1.
    assert r["diagnostic_status"] == "FAIL_CORRELATION"


def test_latency_keeps_signed_physical_time_without_shift():
    actual = wave([0., 1., 1.2, 2.2, 3., 4.], [0., 0., 0., 1., 1., 1.])
    m = event_measurements(actual, "out", "supply", EVENT, DEFINITION)
    assert m["latency_s"] == pytest.approx(.2)
    assert m["transition_10_90_s"] == pytest.approx(.8)
    assert m["settling_after_ramp_s"] == pytest.approx(.18)


def test_settling_does_not_accept_first_entry_before_a_late_excursion():
    a = wave([0., 1., 2., 2.5, 2.6, 3., 4.], [0., 0., 1., 1., 1.2, 1., 1.])
    m = event_measurements(a, "out", "supply", EVENT, DEFINITION)
    assert m["settling_after_ramp_s"] > .6


def test_zero_amplitude_response_is_not_a_zero_latency_claim():
    a = wave([0., 4.], [1., 1.])
    m = event_measurements(a, "out", "supply", EVENT, DEFINITION)
    assert m["timing_status"] == "NOT_OBSERVABLE" and m["latency_s"] is None


def test_crossover_interior_near_zero_cannot_hide_behind_passing_knots():
    reference = wave([0., 1.], [-1., 1.])
    actual = wave([0., 1.], [-.98, 1.02])
    r = compare(actual, reference)
    assert r["diagnostic_status"] == "FAIL_CORRELATION"
    assert r["waveforms"]["voltage"]["samples"] > 2


def test_candidate_timing_thresholds_use_golden_levels():
    reference = wave([0., 1., 2., 3., 4.], [0., 0., 1., 1., 1.])
    actual = wave([0., 1., 2., 3., 4.], [0., 0., .6, .6, .6])
    r = compare_trajectories(actual, reference, output="out", current="supply", events=[EVENT],
                             definition=DEFINITION, policy=policy())
    e = r["events"][0]
    assert e["actual"]["final_v"] == 1.
    assert e["actual"]["observed_final_v"] == pytest.approx(.6)
    assert e["comparisons"]["transition_10_90_s"]["status"] == "FAIL_NOT_REACHED"


@pytest.mark.parametrize("rows", ["0,0\n0,1\n", "0,nan\n1,1\n", "0,0\n.5,1\n"])
def test_bad_waveform_cannot_reach_correlation(tmp_path, rows):
    p = tmp_path/"wave.csv"
    p.write_text("time[s],out[V]\n"+rows)
    with pytest.raises(ValueError):
        read_waveform(p, {"out": "V"}, 1.)
