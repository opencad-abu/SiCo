"""Cartesian, dB, principal phase, units and transfer provenance regression."""

import math
from copy import deepcopy

import pytest
from ac_fixture import recipe, wave
from test_circuit_rc_gui import mcp_call
from test_waveform_measure import quantity

from cadai.ac_measure import measure_ac, validate_ac_samples
from cadai.ac_tools import call_ac
from cadai.mcp import McpServer
from cadai.result_contract import ResultError
from cadai.result_tools import ResultStore


@pytest.mark.parametrize(
    "op,unit,expected",
    [
        ("magnitude", "mV", 5000),
        ("magnitude_db", "dB", 20 * math.log10(5)),
        ("phase", "deg", math.degrees(math.atan2(4, 3))),
        ("phase", "rad", math.atan2(4, 3)),
    ],
)
def test_known_phasor(op, unit, expected):
    row = measure_ac(wave(), [recipe(op, unit)])[0]
    assert row["status"] == "scalar" and row["value"] == pytest.approx(expected)


@pytest.mark.parametrize(
    "imaginary,expected", [(0.0, 180), (-0.0, 180), (1e-6, 179.9999427), (-1e-6, -179.9999427)]
)
def test_principal_negative_real_axis(imaginary, expected):
    row = measure_ac(wave([[100, -1, imaginary], [1000, -1, imaginary]]), [recipe("phase")])[0]
    assert row["value"] == pytest.approx(expected)


def test_cartesian_frequency_interpolation_not_polar_or_logarithmic():
    data = wave([[100, 1, 0], [1000, -1, 0]])
    at = quantity(550, "Hz")
    rows = measure_ac(data, [recipe("magnitude", at=at), recipe("phase", at=at)])
    assert rows[0]["value"] == 0
    assert rows[1]["status"] == "error" and rows[1]["value"] is None
    assert rows[0]["components"]["signal"]["fraction"] == 0.5


def test_transfer_independent_grids_units_and_phase_reference():
    first = wave([[0.1, 1000, 1000], [1, 1000, 1000]], x_unit="kHz", y_unit="mV")
    second = wave([[100, 2, -2], [300, 2, -2], [1000, 2, -2]])
    second["source"]["session"] = "reopened"
    recipes = [
        recipe("magnitude", "1"),
        recipe("phase", magnitude_floor=quantity(0, "1")),
        recipe("magnitude_db", reference=quantity(2, "1")),
    ]
    rows = measure_ac(first, recipes, second=second, denominator_floor=quantity(1, "mV"))
    assert [r["value"] for r in rows] == pytest.approx([2, -90, 0])
    assert rows[0]["components"]["input"]["unit"] == "V"
    assert rows[0]["components"]["phasor"]["imaginary"] == -2


@pytest.mark.parametrize(
    "op,change,reason",
    [
        ("phase", dict(magnitude_floor=quantity(5, "V")), "phase_magnitude_at_or_below_floor"),
        ("phase", dict(result_unit="degrees"), "phase_unit_must_be_deg_or_rad"),
        ("phase", dict(magnitude_floor=quantity(0, "A")), "unit_incompatible"),
        ("magnitude", dict(result_unit="A"), "unit_incompatible"),
        ("magnitude", dict(at=quantity(10, "Hz")), "signal:out_of_range"),
        ("magnitude", dict(at=quantity(1, "s")), "frequency_unit_incompatible"),
        ("magnitude_db", dict(result_unit="V"), "db_result_unit_required"),
    ],
)
def test_measurement_issues_are_not_zero(op, change, reason):
    row = measure_ac(wave(), [recipe(op, **change)])[0]
    assert row["value"] is None and row["reason"] == reason


@pytest.mark.parametrize("status", ["scalar", "family", "missing", "error"])
def test_nonwaveform_type_is_preserved(status):
    row = measure_ac(wave([], status=status), [recipe("magnitude")])[0]
    assert row["status"] == "missing" and row["reason"] == "signal:source_" + status


@pytest.mark.parametrize(
    "samples",
    [
        [[0, 1]],
        [[-1, 1, 0]],
        [[1, 1, 0], [1, 2, 0]],
        [[2, 1, 0], [1, 1, 0]],
        [[1, float("nan"), 0]],
        [[True, 1, 0]],
    ],
)
def test_bad_vectors_refused(samples):
    with pytest.raises(ResultError):
        validate_ac_samples(samples)


@pytest.mark.parametrize(
    "field,value",
    [("history", "H.1"), ("point", 2), ("analysis", "tran"), ("result_ref", "results_" + "b" * 64)],
)
def test_transfer_cross_source_refused(field, value):
    first, second = wave(), wave()
    second["source"][field] = value
    with pytest.raises(ResultError):
        measure_ac(
            first, [recipe("magnitude", "1")], second=second, denominator_floor=quantity(0, "V")
        )


def test_zero_db_and_denominator_and_overflow():
    zero = wave([[0, 0, 0], [1000, 0, 0]])
    assert measure_ac(zero, [recipe("magnitude_db")])[0]["value"] is None
    for floor in [0, 1]:
        row = measure_ac(
            zero, [recipe("magnitude", "1")], second=wave(), denominator_floor=quantity(floor, "V")
        )[0]
        assert row["reason"] == "denominator_at_or_below_floor"
    huge = wave([[0, 9e299, 9e299], [1000, 9e299, 9e299]])
    assert measure_ac(huge, [recipe("magnitude")])[0]["reason"] == "numeric_overflow"


def test_endpoint_single_point_and_current_units():
    data = wave([[500, 3, 4]], y_unit="uA")
    assert measure_ac(data, [recipe("magnitude", "mA")])[0]["value"] == pytest.approx(0.005)
    data["x_unit"] = "s"
    assert (
        measure_ac(data, [recipe("magnitude")])[0]["reason"] == "frequency_axis_unit_incompatible"
    )


def test_mcp_persistence_and_validation(tmp_path):
    store = ResultStore(tmp_path)
    ref, _ = store.put("ac_waveform", wave())
    args = dict(waveform_ref=ref, recipes=[recipe("phase")])

    class Offline:
        def call(self, *args):
            pytest.fail("AC measurements should be offline")

    server = McpServer(Offline(), workspace=tmp_path)
    result = mcp_call(server, "measure_ac_waveform", args)
    assert result["all_measured"] and result["spec_qualified"] is None
    assert mcp_call(McpServer(Offline(), workspace=tmp_path), "measure_ac_waveform", args) == result
    assert store.get(result["measurement_ref"])["recipes"] == args["recipes"]
    assert (
        mcp_call(server, "query_ac_waveform", dict(waveform_ref=ref, limit=1))["next_offset"] == 1
    )
    for change in [
        dict(interpolation="log"),
        dict(phase_mode="unwrap"),
        dict(reference=quantity(1, "V")),
        dict(magnitude_floor=quantity(-1, "V")),
    ]:
        bad = deepcopy(args)
        bad["recipes"][0].update(change)
        with pytest.raises(ResultError):
            call_ac("measure_ac_waveform", bad, workspace=tmp_path, client=None)
