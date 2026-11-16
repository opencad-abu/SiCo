"""Metamorphic tests: names, dimensions and scales are data, not circuit selectors."""

import ast
from copy import deepcopy
from pathlib import Path

import pytest
from ac_spec_fixture import fixture
from test_waveform_measure import quantity, recipe
from test_waveform_pair import pair_recipe, paired
from test_waveform_specs import evaluate

from cadai.waveform_measure import measure
from cadai.waveform_pair import measure_pair


@pytest.mark.parametrize(
    "label,scale,unit,y_scale",
    [
        ("arbitrary_sensor", 1, "V", 1),
        ("unrelated_receiver", 100000, "mV", 1000),
        ("current_monitor", 0.001, "A", 1),
        ("xyz_no_topology", 37, "uA", 1e6),
    ],
)
def test_ac_specs_independent_of_project_names_scales_and_voltage_current(
    tmp_path, label, scale, unit, y_scale
):
    store, args, _, _ = fixture(tmp_path, label=label, scale=scale, unit=unit, y_scale=y_scale)
    report = evaluate(tmp_path, args)
    assert report["qualification"] == "pass" and report["counts"]["pass"] == 14
    rows = store.get(report["report_ref"])["rows"]
    for row in rows:
        if row["metric"] == "bandwidth":
            assert row["value"] / scale == pytest.approx(65.555555556)
        elif row["metric"] == "ratio":
            assert row["value"] == pytest.approx(1.1)
        assert row["test"] == label
        assert all(s["target"][0] == label + "Lib" for s in row["sources"].values())


@pytest.mark.parametrize(
    "time_scale,unit,y_scale", [(1, "V", 1), (1e-6, "mV", 1000), (1e3, "uA", 1e6)]
)
def test_real_measurement_arbitrary_piecewise_paths_and_scaling(time_scale, unit, y_scale):
    base = "A" if unit.endswith("A") else "V"
    first = paired()
    first["source"].update(
        target=["unrelated", "instrument", "maestro"],
        test="characterize",
        signal="/hierarchical/arbitrary",
        library_path="/project/other",
    )
    first.update(
        y_unit=unit, samples=[[x * time_scale, y * y_scale] for x, y in [(0, -2), (2, 4), (5, 1)]]
    )
    window = dict(start=quantity(0, "s"), stop=quantity(5 * time_scale, "s"))
    specs = [
        recipe(op, result_unit=base, window=window) for op in ("minimum", "maximum", "mean", "rms")
    ]
    specs += [recipe("sample", at=quantity(time_scale, "s"), result_unit=base)]
    rows = measure(first, specs)
    assert [r["value"] for r in rows] == pytest.approx([-2, 4, 1.9, (29 / 5) ** 0.5, 1])
    second = deepcopy(first)
    second["source"]["signal"] = "/hierarchical/other"
    second["samples"] = [[x, -3 * y] for x, y in first["samples"]]
    gain = [
        pair_recipe(
            "gain_sample", at=quantity(time_scale, "s"), denominator_floor=quantity(0, base)
        ),
        pair_recipe("gain_rms", window=window, denominator_floor=quantity(0, base)),
    ]
    assert [r["value"] for r in measure_pair(first, second, gain)] == pytest.approx([-3, 3])
    second["samples"] = [[x + 0.7 * time_scale, y] for x, y in first["samples"]]
    event = dict(window=window, threshold=quantity(0, base), direction="rising", occurrence=1)
    later = dict(
        event,
        window=dict(start=quantity(0.7 * time_scale, "s"), stop=quantity(5.7 * time_scale, "s")),
    )
    delay = pair_recipe("delay", input_event=event, output_event=later, result_unit="s")
    assert measure_pair(first, second, [delay])[0]["value"] == pytest.approx(0.7 * time_scale)


def test_product_measurement_modules_do_not_import_test_fixtures():
    package = Path(__file__).parents[1] / "cadai"
    paths = sorted(
        set(package.glob("waveform*.py"))
        | set(package.glob("ac_*.py"))
        | set(package.glob("measurement_*.py"))
    )
    paths += [package / "result_tools.py", package / "result_contract.py"]
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            imports = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            assert all(
                not any(
                    p == "tests" or p.startswith("test_") or "fixture" in p for p in name.split(".")
                )
                for name in imports
            ), path
