"""Event-anchored transition-response sampling schedule."""

from __future__ import annotations

from aivworkbench.plugins.transition_response import schedule


def _fixture(start_s, end_s, *, fine_scope=None):
    spec = {"fine_step_s": 1e-9, "pre_s": 2e-8, "tail_s": 5e-8, "timeprecision_s": 1e-12}
    if fine_scope is not None:
        spec["fine_scope"] = fine_scope
    model = {"step_s": 2e-8, "transition_response": spec}
    plan = {"analysis": {"stop_s": 2e-4},
            "roles": {"supply": "VDD", "controls": ["EN"], "output": "VOUT"}}
    case = {"events": [{"id": "edge", "signal": "VDD", "start_s": start_s, "end_s": end_s}],
            "voltages": {"VDD": {"pwl": [[0.0, 0.0], [start_s, 0.0], [end_s, 5.0], [2e-4, 5.0]]},
                         "EN": {"dc_v": 0.0}},
            "load_ohm": 10000.0, "load_conductance_pwl_s": None}
    return model, plan, case


def test_off_grid_onset_is_sampled_exactly():
    start, end = 96.309855604192485e-6, 123.89526031159244e-6
    ticks = schedule(*_fixture(start, end))
    # Floor at the onset and ceiling at the end keep the first and last
    # rising differences on the prescribed slope.
    assert any(start - 1e-9 <= t <= start for t in ticks)
    assert any(end <= t <= end + 2e-12 for t in ticks)
    slope = 5.0/(end - start)
    def voltage(t):
        return 0.0 if t <= start else min(5.0, slope*(t-start))
    window = [t for t in ticks if start - 1e-9 <= t <= end + 1e-9]
    rates = [(voltage(b)-voltage(a))/(b-a) for a, b in zip(window, window[1:])
             if b > a and voltage(b) > voltage(a)]
    # The candidate guards evaluate the last rising difference before the
    # plateau; every rising difference must stay inside the fitted domain.
    assert rates and all(124999.9875 <= rate <= 2500000.25 for rate in rates)
    assert min(ticks) == 0.0 and max(ticks) == 2e-4
    assert list(ticks) == sorted(set(ticks))


def test_grid_aligned_public_onset_keeps_the_global_grid():
    start, end = 1e-4, 1.02e-4
    ticks = schedule(*_fixture(start, end))
    assert any(abs(t - start) < 1e-15 for t in ticks)
    # Every fine tick is still a global multiple of the fine step.
    assert all(abs(t/1e-9 - round(t/1e-9)) < 1e-6 for t in ticks if t <= 1.2e-4)


def test_endpoint_augmented_events_skip_the_extra_anchor():
    # The end rounds down on the femtosecond grid (fraction < 0.5 fs), which
    # is exactly the case where a ceiling anchor would add a stray tick.
    start, end = 96.309855604192485e-6, 123.89526031103894e-6
    model, plan, case = _fixture(start, end, fine_scope="startup_and_control")
    model["transition_response"].update({"endpoint_step_s": 1e-13, "endpoint_window_s": 1e-10,
                                         "endpoint_scope": "startup", "timeprecision_s": 1e-15})
    plan["roles"]["controls"] = ["EN"]
    ticks = schedule(model, plan, case)
    # The augmentation itself anchors the rounded endpoints exactly...
    assert any(abs(t - start) <= 1e-15 for t in ticks)
    assert any(abs(t - end) <= 1e-15 for t in ticks)
    # ...and no sub-picosecond anchor tick is inserted just past the plateau.
    assert not any(end < t < end + 5e-14 for t in ticks)
