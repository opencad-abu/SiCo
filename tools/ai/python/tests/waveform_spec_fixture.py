"""Synthetic exact-history artifacts for specification tests; all limits are fixture data."""

from copy import deepcopy

from test_waveform_measure import recipe
from test_waveform_pair import pair_recipe, paired

from cadai.result_contract import SCHEMA
from cadai.result_tools import ResultStore
from cadai.waveform_tools import call_waveform


def fixture(tmp_path, first_status="done"):
    store = ResultStore(tmp_path)
    sample = recipe("sample", result_unit="mV")
    delay = pair_recipe("delay", result_unit="ms")
    snapshot = dict(
        schema=SCHEMA,
        target=["work", "tb", "maestro"],
        library_path="/project/work",
        history="H.0",
        coordinates=[],
        tests=[],
        outputs=[],
        definitions=[],
    )
    points = [dict(test="test", corner="cold", point=1), dict(test="test", corner="hot", point=2)]
    for point in points:
        snapshot["coordinates"].append(
            dict(
                corner=point["corner"],
                point=point["point"],
                parameters=[dict(name="vdd", kind="design", value=point["point"])],
            )
        )
        snapshot["tests"].append(dict(point, status="done", info=None))
    snapshot["tests"][0]["status"] = first_status
    result_ref, _ = store.put("results", snapshot)
    refs = []
    singles = []
    pairs = []
    for index, point in enumerate(points):
        a = paired()
        a["source"].update(
            **point, result_ref=result_ref, coordinate=snapshot["coordinates"][index]
        )
        b = deepcopy(a)
        b["source"]["signal"] = "/output"
        b["samples"] = [[0, 0], [0.3, 0.6], [2, 4]]
        ar, _ = store.put("waveform", a)
        br, _ = store.put("waveform", b)
        single = call_waveform(
            "measure_waveform",
            dict(waveform_ref=ar, recipes=[sample]),
            workspace=tmp_path,
            client=None,
        )
        pair = call_waveform(
            "measure_waveform_pair",
            dict(input_ref=ar, output_ref=br, recipes=[delay]),
            workspace=tmp_path,
            client=None,
        )
        singles.append(single)
        pairs.append(pair)
        refs.extend([single["measurement_ref"], pair["measurement_ref"]])
    contract = dict(
        source="Explicit synthetic linear-waveform acceptance",
        expected_test_points=points,
        specifications=[
            dict(
                test="test",
                analysis="tran",
                kind="single",
                signals=dict(signal="/input"),
                recipe=sample,
                unit="V",
                lower=0.4,
                upper=0.6,
            ),
            dict(
                test="test",
                analysis="tran",
                kind="pair",
                signals=dict(input="/input", output="/output"),
                recipe=delay,
                unit="s",
                lower=-0.6,
                upper=-0.4,
            ),
        ],
    )
    return (
        store,
        dict(result_ref=result_ref, measurement_refs=refs, contract=contract),
        singles,
        pairs,
    )
