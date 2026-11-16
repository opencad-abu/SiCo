"""Analytic examples and failures, including unequal sample spacing and units."""

import math

import pytest

from cadai.result_contract import ResultError
from cadai.result_tools import ResultStore
from cadai.waveform_measure import measure, validate_samples
from cadai.waveform_schema import WAVE_SCHEMA
from cadai.waveform_tools import call_waveform


def quantity(value, unit="s"):
    return dict(value=value, unit=unit)


def recipe(op, **extra):
    result = dict(id=op, operation=op, interpolation="linear", result_unit="V")
    if op == "sample":
        result["at"] = quantity(0.5)
    else:
        result["window"] = dict(start=quantity(0), stop=quantity(2))
    if op == "crossing":
        result.update(threshold=quantity(1, "V"), direction="rising", occurrence=1, result_unit="s")
    return dict(result, **extra)


def wave(samples=None, **extra):
    return dict(
        schema=WAVE_SCHEMA,
        source={"history": "H.0"},
        status="waveform",
        x_unit="s",
        y_unit="V",
        samples=[[0, 0], [0.2, 0.2], [2, 2]] if samples is None else samples,
        **extra,
    )


@pytest.mark.parametrize(
    "op,expected",
    [
        ("sample", 0.5),
        ("minimum", 0),
        ("maximum", 2),
        ("mean", 1),
        ("rms", 2 / math.sqrt(3)),
        ("crossing", 1),
    ],
)
def test_known_ramp_unequal_sampling(op, expected):
    result = measure(wave(), [recipe(op)])[0]
    assert result["status"] == "scalar"
    assert result["value"] == pytest.approx(expected)


@pytest.mark.parametrize(
    "op,expected",
    [
        ("minimum", 0.4),
        ("maximum", 1.2),
        ("mean", 0.8),
        ("rms", math.sqrt((0.4 * 0.4 + 0.4 * 1.2 + 1.2 * 1.2) / 3)),
    ],
)
def test_window_endpoints_interpolated(op, expected):
    spec = recipe(
        op, window=dict(start=quantity(400, "ms"), stop=quantity(1200, "ms")), result_unit="mV"
    )
    assert measure(wave(), [spec])[0]["value"] == pytest.approx(expected * 1000)


@pytest.mark.parametrize(
    "spec,reason",
    [
        (recipe("sample", at=quantity(-1)), "out_of_range"),
        (recipe("sample", at=quantity(3)), "out_of_range"),
        (recipe("sample", at=quantity(1, "V")), "unit_incompatible"),
        (recipe("mean", window=dict(start=quantity(2), stop=quantity(2))), "invalid_window"),
        (recipe("mean", window=dict(start=quantity(0), stop=quantity(3))), "out_of_range"),
        (recipe("crossing", direction="falling"), "no_crossing"),
        (recipe("crossing", occurrence=2), "no_crossing"),
        (recipe("crossing", threshold=quantity(3, "V")), "no_crossing"),
        (recipe("sample", result_unit="A"), "unit_incompatible"),
    ],
)
def test_boundary_reasons(spec, reason):
    row = measure(wave(), [spec])[0]
    assert row["reason"] == reason and row["value"] is None


def test_empty_constant_exact_endpoints_and_plateau():
    assert measure(wave([]), [recipe("mean")])[0]["reason"] == "empty_waveform"
    for x in [0, 2]:
        assert measure(wave(), [recipe("sample", at=quantity(x))])[0]["value"] == x
    constant = wave([[0, 1], [1, 1], [2, 1]])
    assert measure(constant, [recipe("rms")])[0]["value"] == 1
    assert measure(constant, [recipe("crossing")])[0]["reason"] == "no_crossing"
    plateau = wave([[0, 0], [0.5, 1], [1, 1], [1.5, 2], [2, 0]])
    assert measure(plateau, [recipe("crossing")])[0]["value"] == 0.5
    assert (
        measure(plateau, [recipe("crossing", direction="either", occurrence=2)])[0]["value"] == 1.75
    )


@pytest.mark.parametrize("status", ["scalar", "complex", "family", "missing", "error"])
def test_non_waveform_never_becomes_first_member(status):
    data = wave([])
    data["status"] = status
    row = measure(data, [recipe("sample")])[0]
    assert row["reason"] == "source_" + status and row["value"] is None


@pytest.mark.parametrize(
    "samples",
    [
        [[0, 0], [0, 1]],
        [[1, 0], [0, 1]],
        [[True, 0]],
        [[0, float("nan")]],
        [[0, float("inf")]],
        [[0]],
    ],
)
def test_invalid_vectors_rejected(samples):
    with pytest.raises(ResultError):
        validate_samples(samples)


def test_missing_unknown_and_case_sensitive_units():
    data = wave()
    data["y_unit"] = None
    assert measure(data, [recipe("sample")])[0]["reason"] == "unit_unknown"
    data["y_unit"] = "custom"
    assert measure(data, [recipe("sample", result_unit="custom")])[0]["value"] == 0.5
    assert measure(data, [recipe("sample")])[0]["reason"] == "unit_incompatible"
    assert measure(wave(), [recipe("sample", result_unit="MV")])[0]["value"] == pytest.approx(
        0.5e-6
    )


def test_offline_mcp_artifact_and_schema_guards(tmp_path):
    store = ResultStore(tmp_path)
    ref, artifact = store.put("waveform", wave())

    def call(name, **args):
        return call_waveform(name, args, workspace=tmp_path, client=None)

    result = call(
        "measure_waveform", waveform_ref=ref, recipes=[recipe("mean"), recipe("crossing")]
    )
    assert result["all_measured"] and result["spec_qualified"] is None
    assert store.get(result["measurement_ref"])["rows"] == result["rows"]
    assert (
        call("measure_waveform", waveform_ref=ref, recipes=[recipe("mean"), recipe("crossing")])
        == result
    )
    assert call("query_waveform", waveform_ref=ref, offset=1, limit=1)["next_offset"] == 2
    for specs in [
        [recipe("mean"), recipe("mean")],
        [recipe("sample", window={})],
        [recipe("sample", interpolation="nearest")],
        [recipe("crossing", occurrence=True)],
        [recipe("sample", at=quantity(float("nan")))],
    ]:
        with pytest.raises(ResultError):
            call("measure_waveform", waveform_ref=ref, recipes=specs)
    from pathlib import Path

    Path(artifact["path"]).write_text("{}")
    with pytest.raises(ResultError):
        call("query_waveform", waveform_ref=ref)
