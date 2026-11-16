"""Analytic paired signals on unequal grids, provenance and failure contracts."""

import copy
import math

import pytest
from test_circuit_rc_gui import mcp_call
from test_waveform_measure import quantity, wave

from cadai.mcp import McpServer
from cadai.result_contract import ResultError
from cadai.result_tools import ResultStore
from cadai.waveform_pair import measure_pair
from cadai.waveform_tools import call_waveform


def paired(samples=None, signal="/input"):
    result = wave(samples)
    result["source"] = dict(
        target=["work", "tb", "maestro"],
        library_path="/project/work",
        session="session1",
        history="H.0",
        test="test",
        corner="corner",
        point=1,
        analysis="tran",
        coordinate=dict(corner="corner", point=1, parameters=[]),
        results_directory="/simulation/point",
        result_ref="results_" + "a" * 64,
        reader="maeReadResDB/getData/DR",
        signal=signal,
    )
    return result


def pair_recipe(op, **extra):
    window = dict(start=quantity(0), stop=quantity(2))
    result = dict(id=op, operation=op, interpolation="linear", result_unit="1")
    if op == "delay":
        event = dict(window=window, threshold=quantity(1, "V"), direction="rising", occurrence=1)
        result.update(
            result_unit="s",
            pairing="explicit_occurrences",
            input_event=copy.deepcopy(event),
            output_event=copy.deepcopy(event),
        )
    else:
        result.update(denominator_floor=quantity(0, "V"))
        result.update(at=quantity(0.5)) if op == "gain_sample" else result.update(window=window)
    return dict(result, **extra)


@pytest.mark.parametrize("op,expected", [("delay", -0.5), ("gain_sample", 2), ("gain_rms", 2)])
def test_independent_grids_analytic(op, expected):
    # y_input=x; y_output=2x with different internal sample times.
    first, second = paired(), paired([[0, 0], [0.3, 0.6], [1, 2], [2, 4]], "/output")
    row = measure_pair(first, second, [pair_recipe(op)])[0]
    assert row["status"] == "scalar" and row["value"] == pytest.approx(expected)
    assert row["components"]["input"]["unit"] == ("s" if op == "delay" else "V")


def test_mixed_axis_and_amplitude_scales():
    first, second = paired(), paired([[0, 0], [300, 600], [2000, 4000]], "/output")
    second.update(x_unit="ms", y_unit="mV")
    for op, expected in [("delay", -0.5), ("gain_sample", 2), ("gain_rms", 2)]:
        assert measure_pair(first, second, [pair_recipe(op)])[0]["value"] == pytest.approx(expected)


def test_delay_signed_explicit_occurrences_and_missing_target():
    first = paired([[0, 0], [0.5, 2], [1, 0], [1.5, 2], [2, 0]])
    second = paired([[0, 0], [0.2, 0], [0.7, 2], [1.2, 0], [1.7, 2], [2, 0]], "/output")
    spec = pair_recipe("delay")
    spec["input_event"]["occurrence"] = 2
    row = measure_pair(first, second, [spec])[0]
    assert row["value"] == pytest.approx(-0.8)  # Do not substitute the following output edge.
    spec["output_event"]["occurrence"] = 2
    assert measure_pair(first, second, [spec])[0]["value"] == pytest.approx(0.2)
    spec["output_event"]["occurrence"] = 3
    row = measure_pair(first, second, [spec])[0]
    assert row["reason"] == "output:no_crossing" and row["value"] is None
    assert row["components"]["input"]["value"] == pytest.approx(1.25)


def test_falling_edge_and_zero_delay():
    data = paired([[0, 2], [0.1, 1.8], [2, -2]])
    spec = pair_recipe("delay")
    for role in ["input_event", "output_event"]:
        spec[role].update(direction="falling", threshold=quantity(0, "V"))
    assert measure_pair(data, data, [spec])[0]["value"] == 0


@pytest.mark.parametrize("op", ["gain_sample", "gain_rms"])
@pytest.mark.parametrize(
    "amplitude,floor,reason",
    [
        (0, 0, "denominator_at_or_below_floor"),
        (1e-6, 1e-6, "denominator_at_or_below_floor"),
        (-1e-6, 2e-6, "denominator_at_or_below_floor"),
    ],
)
def test_zero_and_near_zero_denominator(op, amplitude, floor, reason):
    first = paired([[0, amplitude], [2, amplitude]])
    result = measure_pair(
        first, paired(), [pair_recipe(op, denominator_floor=quantity(floor, "V"))]
    )[0]
    assert result["status"] == "error" and result["reason"] == reason and result["value"] is None


def test_gain_sign_and_rms_includes_dc():
    first, second = paired([[0, 1], [2, 1]]), paired([[0, -2], [2, -2]], "/output")
    assert measure_pair(first, second, [pair_recipe("gain_sample")])[0]["value"] == -2
    assert measure_pair(first, second, [pair_recipe("gain_rms")])[0]["value"] == 2
    second["samples"] = [[0, -1], [2, 3]]
    assert measure_pair(first, second, [pair_recipe("gain_rms")])[0]["value"] == pytest.approx(
        math.sqrt(7 / 3)
    )


@pytest.mark.parametrize(
    "field",
    [
        "target",
        "library_path",
        "history",
        "test",
        "corner",
        "point",
        "analysis",
        "coordinate",
        "results_directory",
        "result_ref",
        "reader",
    ],
)
def test_cross_coordinate_refused(field):
    first, second = paired(), paired()
    second["source"][field] = ["changed"] if field == "target" else "different"
    with pytest.raises(ResultError):
        measure_pair(first, second, [pair_recipe("gain_sample")])


def test_reopened_session_same_point_allowed_and_incomplete_identity_refused():
    first, second = paired(), paired(signal="/output")
    second["source"]["session"] = "reopened"
    assert measure_pair(first, second, [pair_recipe("gain_sample")])[0]["value"] == 1
    del second["source"]["coordinate"]
    with pytest.raises(ResultError):
        measure_pair(first, second, [pair_recipe("gain_sample")])


@pytest.mark.parametrize("status", ["family", "complex", "scalar", "missing", "error"])
def test_missing_family_or_nonreal_never_flattened(status):
    first = paired([])
    first["status"] = status
    row = measure_pair(first, paired(), [pair_recipe("gain_sample")])[0]
    assert row["reason"] == "input:source_" + status and row["value"] is None


@pytest.mark.parametrize("op", ["gain_sample", "gain_rms", "delay"])
def test_no_extrapolation_and_empty(op):
    second = paired([[0.6, 0.6], [1.8, 1.8]], "/output")
    row = measure_pair(paired(), second, [pair_recipe(op)])[0]
    assert row["reason"] == "output:out_of_range"
    assert (
        measure_pair(paired([]), second, [pair_recipe(op)])[0]["reason"] == "input:empty_waveform"
    )


@pytest.mark.parametrize(
    "which,unit,reason",
    [
        ("x_unit", "Hz", "axis_unit_incompatible"),
        ("x_unit", None, "axis_unit_unknown"),
        ("y_unit", "A", "gain_unit_incompatible"),
        ("y_unit", "custom", "gain_unit_incompatible"),
        ("y_unit", None, "gain_unit_unknown"),
    ],
)
def test_incompatible_units(which, unit, reason):
    second = paired()
    second[which] = unit
    assert measure_pair(paired(), second, [pair_recipe("gain_sample")])[0]["reason"] == reason


def test_frequency_axis_is_not_time_delay():
    first, second = paired(), paired()
    first["x_unit"] = second["x_unit"] = "Hz"
    assert (
        measure_pair(first, second, [pair_recipe("delay")])[0]["reason"]
        == "delay_requires_time_axis"
    )


def test_mcp_persistence_schema_failure_and_offline(tmp_path):
    class NoNative:
        def call(self, *args):
            pytest.fail("pair measurements must stay offline")

    store = ResultStore(tmp_path)
    a, _ = store.put("waveform", paired())
    b, _ = store.put("waveform", paired(signal="/output"))
    server = McpServer(NoNative(), workspace=tmp_path)
    args = dict(
        input_ref=a, output_ref=b, recipes=[pair_recipe("gain_sample"), pair_recipe("delay")]
    )
    report = mcp_call(server, "measure_waveform_pair", args)
    assert report["all_measured"] and report["spec_qualified"] is None
    assert report == mcp_call(server, "measure_waveform_pair", args)
    assert store.get(report["measurement_ref"])["rows"] == report["rows"]
    assert report["sources"]["input"]["signal"] != report["sources"]["output"]["signal"]
    for recipe in [
        pair_recipe("delay", pairing="next_edge"),
        pair_recipe("gain_rms", at=quantity(0)),
        pair_recipe("gain_sample", denominator_floor=quantity(-1, "V")),
        pair_recipe("gain_sample", denominator_floor=quantity(True, "V")),
    ]:
        with pytest.raises(ResultError):
            call_waveform(
                "measure_waveform_pair",
                dict(args, recipes=[recipe]),
                workspace=tmp_path,
                client=None,
            )
    with pytest.raises(ResultError):
        call_waveform(
            "measure_waveform_pair",
            dict(args, recipes=[args["recipes"][0]] * 2),
            workspace=tmp_path,
            client=None,
        )


@pytest.mark.parametrize("op", ["gain_sample", "gain_rms"])
def test_current_gain_and_unknown_result_units(op):
    first, second = paired([[0, 2], [2, 2]]), paired([[0, 6000], [2, 6000]])
    first["y_unit"], second["y_unit"] = "A", "mA"
    spec = pair_recipe(op, denominator_floor=quantity(1, "mA"))
    assert measure_pair(first, second, [spec])[0]["value"] == 3
    spec["result_unit"] = "dB"
    assert measure_pair(first, second, [spec])[0]["reason"] == "gain_result_unit_must_be_1"
    spec.update(result_unit="1", denominator_floor=quantity(1, "V"))
    assert measure_pair(first, second, [spec])[0]["reason"] == "unit_incompatible"


def test_delay_windows_and_threshold_units():
    spec = pair_recipe("delay")
    spec["output_event"]["window"]["start"] = quantity(3)
    assert measure_pair(paired(), paired(), [spec])[0]["reason"] == "output:invalid_window"
    spec = pair_recipe("delay", result_unit="V")
    assert measure_pair(paired(), paired(), [spec])[0]["reason"] == "unit_incompatible"
    spec = pair_recipe("delay")
    spec["input_event"]["threshold"] = quantity(1, "A")
    assert measure_pair(paired(), paired(), [spec])[0]["reason"] == "input:unit_incompatible"


def test_numeric_overflow_does_not_serialize_infinity():
    first = paired([[0, 1e-200], [2, 1e-200]])
    second = paired([[0, 1e200], [2, 1e200]])
    row = measure_pair(first, second, [pair_recipe("gain_sample")])[0]
    assert row["status"] == "error" and row["reason"] == "numeric_overflow" and row["value"] is None
