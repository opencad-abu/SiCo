"""Synthetic and native AC fixture data; no circuit defaults in product code."""

from test_waveform_measure import quantity
from test_waveform_pair import paired
from waveform_pair_fixture import setup_recipe

from cadai.ac_schema import AC_SCHEMA


def wave(samples=None, **extra):
    data = paired()
    data.update(
        schema=AC_SCHEMA,
        representation="cartesian_complex",
        native_y_type="doublecomplex",
        x_unit="Hz",
        y_unit="V",
        samples=[[100, 3, 4], [1000, 3, 4]] if samples is None else samples,
    )
    data["source"]["analysis"] = "ac"
    data.update(extra)
    return data


def recipe(op, unit=None, **extra):
    data = dict(
        id=op,
        operation=op,
        at=quantity(500, "Hz"),
        interpolation="cartesian_linear_frequency",
        result_unit=unit or {"magnitude": "V", "magnitude_db": "dB", "phase": "deg"}[op],
    )
    if op == "phase":
        data.update(phase_mode="principal", magnitude_floor=quantity(0, "V"))
    if op == "magnitude_db":
        data["reference"] = quantity(1, "V")
    data.update(extra)
    return data


def ac_setup():
    data = setup_recipe()
    data["target"]["cell"] = "ac_characterization"
    test = data["tests"][0]
    test["name"] = "small_signal"
    test["analyses"] = [
        dict(name="ac", options=dict(start="100", stop="10M", dec="80")),
        dict(name="dc", options=dict(saveOppoint=True)),
    ]
    test["outputs"] += [
        dict(
            name="saved_mag",
            kind="expression",
            value='mag(value(VF("/OUT") 1e6))',
            plot=True,
            save=True,
        ),
        dict(
            name="saved_phase",
            kind="expression",
            value='phaseDeg(value(VF("/OUT") 1e6))',
            plot=True,
            save=True,
        ),
    ]
    return data


def point_recipes(frequency, transfer=False):
    unit = "1" if transfer else "V"
    at = quantity(frequency / 1000, "kHz")
    return [
        recipe("magnitude", "1" if transfer else "mV", at=at),
        recipe("magnitude_db", at=at, reference=quantity(1, unit)),
        recipe("phase", at=at, magnitude_floor=quantity(1e-12, unit)),
        recipe("phase", "rad", id="phase_rad", at=at, magnitude_floor=quantity(1e-12, unit)),
    ]
