"""Explicit project-free RC characterization data for native pair acceptance."""

from copy import deepcopy

from test_waveform_measure import quantity
from test_waveform_pair import pair_recipe


def setup_recipe():
    return dict(
        schema="cad.simulation.recipe.v1",
        target=dict(library="genericWork", cell="paired_measurements", view="maestro"),
        variables=dict(RVAL="1000"),
        temperature_c=27,
        corners=[
            dict(name=n, temperature_c=t, variables={}, models=[])
            for n, t in [("cool", 10), ("warm", 60)]
        ],
        sweeps=[dict(variable="RVAL", values=[1000, 2000])],
        tests=[
            dict(
                name="transient_response",
                design=dict(library="genericWork", cell="paired_rc", view="schematic"),
                simulator="spectre",
                variables={},
                models=[],
                analyses=[
                    dict(
                        name="tran",
                        options=dict(stop="8u", maxstep="10n", errpreset="conservative"),
                    )
                ],
                outputs=[
                    dict(name=n, kind="signal", value="/" + n, plot=True, save=True)
                    for n in ["IN", "OUT"]
                ],
                switch_views=["spectre", "schematic"],
                stop_views=["spectre"],
            )
        ],
    )


def measurement_recipes():
    delay = pair_recipe("delay", result_unit="ns")
    for role in ["input_event", "output_event"]:
        delay[role].update(
            window=dict(start=quantity(0.5, "us"), stop=quantity(4, "us")),
            threshold=quantity(500, "mV"),
        )
    sample = pair_recipe(
        "gain_sample", at=quantity(2.001, "us"), denominator_floor=quantity(1, "mV")
    )
    rms = pair_recipe(
        "gain_rms",
        window=dict(start=quantity(1.001, "us"), stop=quantity(3.001, "us")),
        denominator_floor=quantity(1, "mV"),
    )
    return deepcopy([delay, sample, rms])
