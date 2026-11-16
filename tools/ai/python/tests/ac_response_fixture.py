"""Explicit response contracts and physical RLC acceptance configuration."""

from ac_fixture import ac_setup
from test_waveform_measure import quantity


def recipe(operation, **extra):
    r = dict(
        id=operation,
        operation=operation,
        window=dict(start=quantity(100, "Hz"), stop=quantity(1e6, "Hz")),
        interpolation="cartesian_linear_frequency",
        direction="falling",
        occurrence=1,
        result_unit="Hz" if operation == "bandwidth" else "deg",
    )
    if operation == "bandwidth":
        r.update(definition="lowpass_cutoff", reference_gain=quantity(1, "1"), drop_db=3)
    else:
        r.update(
            characteristic="one_plus_L",
            loop_sign=1,
            phase_anchor_turns=0,
            phase_mode="continuous_cartesian_path",
            magnitude_floor=quantity(1e-12, "1"),
        )
    r.update(extra)
    return r


def setup():
    data = ac_setup()
    data["target"]["cell"] = "response_measurements"
    data["tests"][0]["design"]["cell"] = "response_rlc"
    data["tests"][0]["analyses"] = [
        dict(name="ac", options=dict(start="100", stop="1M", dec="160"))
    ]
    data["tests"][0]["outputs"] = data["tests"][0]["outputs"][:2]
    return data
