"""Synthetic task data for catalog-driven execution of every published operation."""

from copy import deepcopy

from ac_fixture import recipe as ac_recipe
from ac_fixture import wave as ac_wave
from ac_response_fixture import recipe as response_recipe
from test_waveform_measure import quantity
from test_waveform_measure import recipe as real_recipe
from test_waveform_pair import pair_recipe, paired

from cadai.result_tools import ResultStore


def task(path, kind, operation):
    store = ResultStore(path)
    if kind in {"single", "pair"}:
        first = paired([[0, 0], [0.2, 0.2], [2, 2]])
        second = paired([[0, 0], [0.3, 0.6], [2, 4]], signal="/other")
        ar, _ = store.put("waveform", first)
        br, _ = store.put("waveform", second)
        recipe = real_recipe(operation) if kind == "single" else pair_recipe(operation)
        args = dict(waveform_ref=ar) if kind == "single" else dict(input_ref=ar, output_ref=br)
    else:
        first = ac_wave([[100, 1, 0], [1000, 1, 0]])
        second = deepcopy(first)
        second["samples"] = [[100, 2, 0], [1000, 0.2, 0]]
        second["source"]["signal"] = "/arbitrary"
        ar, _ = store.put("ac_waveform", first)
        br, _ = store.put("ac_waveform", second)
        if kind == "ac_response":
            recipe = response_recipe(
                operation, window=dict(start=quantity(100, "Hz"), stop=quantity(1000, "Hz"))
            )
        else:
            unit = "V" if kind == "ac_signal" else "1"
            recipe = ac_recipe(operation)
            if operation == "magnitude":
                recipe["result_unit"] = unit
            elif operation == "magnitude_db":
                recipe["reference"] = quantity(1, unit)
            else:
                recipe["magnitude_floor"] = quantity(0, unit)
        args = (
            dict(waveform_ref=br)
            if kind == "ac_signal"
            else dict(input_ref=ar, output_ref=br, denominator_floor=quantity(0, "V"))
        )
    return dict(args, recipes=[recipe])
