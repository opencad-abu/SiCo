"""Arbitrary Cartesian paths for AC specs; no circuit topology or PDK assumed."""

import math
from copy import deepcopy

from ac_fixture import recipe as point_recipe
from ac_fixture import wave
from ac_response_fixture import recipe as response_recipe
from test_waveform_measure import quantity

from cadai.ac_response import call_response
from cadai.ac_tools import call_ac
from cadai.result_contract import SCHEMA
from cadai.result_tools import ResultStore


def fixture(path, *, label="probe", scale=1, unit="V", y_scale=1):
    store = ResultStore(path)
    points = [dict(test=label, corner="case_" + str(n), point=n) for n in (7, 13)]
    coordinates = [
        dict(
            corner=p["corner"],
            point=p["point"],
            parameters=[dict(name="setting_" + label, kind="design", value=p["point"])],
        )
        for p in points
    ]
    snapshot = dict(
        schema=SCHEMA,
        target=[label + "Lib", label + "Cell", "maestro"],
        library_path="/projects/" + label,
        history=label + ".19",
        coordinates=coordinates,
        tests=[dict(p, status="done", info=None) for p in points],
        outputs=[],
        definitions=[],
    )
    result_ref, _ = store.put("results", snapshot)
    at = quantity(60 * scale, "Hz")
    base_unit = "A" if unit.endswith("A") else "V"
    recipes = {
        "ac_signal": [
            point_recipe("magnitude", base_unit, id="absolute", at=at),
            point_recipe(
                "phase", "deg", id="absolute_phase", at=at, magnitude_floor=quantity(0, base_unit)
            ),
        ],
        "ac_transfer": [
            point_recipe("magnitude", "1", id="ratio", at=at),
            point_recipe("magnitude_db", id="relative_db", at=at, reference=quantity(0.5, "1")),
            point_recipe(
                "phase", "rad", id="relative_phase", at=at, magnitude_floor=quantity(0, "1")
            ),
        ],
        "ac_response": [
            response_recipe(
                "bandwidth",
                result_unit="kHz",
                reference_gain=quantity(2, "1"),
                drop_db=20 * math.log10(2),
            ),
            response_recipe("phase_margin", result_unit="deg"),
        ],
    }
    for r in recipes["ac_response"]:
        r["window"] = dict(start=quantity(10 * scale, "Hz"), stop=quantity(110 * scale, "Hz"))
    floor = quantity(0, base_unit)
    limits = [
        (base_unit, 3, 4),
        ("rad", -2, -1),
        ("1", 1, 1.2),
        ("dB", 6, 8),
        ("deg", -150, -140),
        ("Hz", 65 * scale, 66 * scale),
        ("rad", 0.6, 0.7),
    ]
    specs = []
    for kind, rs in recipes.items():
        for r in rs:
            comparison_unit, lower, upper = limits[len(specs)]
            specs.append(
                dict(
                    test=label,
                    analysis="ac",
                    kind=kind,
                    signals=(
                        dict(signal="/" + label + "/sense")
                        if kind == "ac_signal"
                        else dict(input="/" + label + "/drive", output="/" + label + "/sense")
                    ),
                    recipe=r,
                    unit=comparison_unit,
                    lower=lower,
                    upper=upper,
                    **({} if kind == "ac_signal" else dict(denominator_floor=floor)),
                )
            )
    contract = dict(
        source="Explicit synthetic Cartesian path specification",
        expected_test_points=points,
        specifications=specs,
    )
    refs, reports, waves = [], [], []
    for p, coordinate in zip(points, coordinates):
        source = dict(
            result_ref=result_ref,
            target=snapshot["target"],
            history=snapshot["history"],
            library_path=snapshot["library_path"],
            coordinate=coordinate,
            **p,
        )
        first = wave(
            [[f * scale, 1.8 * y_scale, 2.4 * y_scale] for f in (10, 47, 110)], y_unit=unit
        )
        first["source"].update(source, signal="/" + label + "/drive")
        second = deepcopy(first)
        second["source"]["signal"] = "/" + label + "/sense"
        # output/input = (-.8-.6j) * linearly decreasing magnitude; output is purely -j.
        second["samples"] = [[10 * scale, 0, -6 * y_scale], [110 * scale, 0, -0.6 * y_scale]]
        ar, _ = store.put("ac_waveform", first)
        br, _ = store.put("ac_waveform", second)
        waves.append((ar, br))
        for kind, rs in recipes.items():
            if kind == "ac_response":
                report = call_response(
                    "measure_ac_response",
                    dict(input_ref=ar, output_ref=br, denominator_floor=floor, recipes=rs),
                    workspace=path,
                )
            else:
                args = (
                    dict(waveform_ref=br)
                    if kind == "ac_signal"
                    else dict(input_ref=ar, output_ref=br, denominator_floor=floor)
                )
                report = call_ac(
                    "measure_ac_waveform" if kind == "ac_signal" else "measure_ac_transfer",
                    dict(args, recipes=rs),
                    workspace=path,
                    client=None,
                )
            refs.append(report.get("measurement_ref", report.get("report_ref")))
            reports.append(report)
    return (
        store,
        dict(result_ref=result_ref, measurement_refs=refs, contract=contract),
        reports,
        waves,
    )
