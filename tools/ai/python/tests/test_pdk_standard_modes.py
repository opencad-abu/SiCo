"""Input modes must expose which dimension is calculated, including the inverted diode rule."""

import pytest

from cadai.pdk_standard.policy_modes import rows


def parameter(write="allow", requirement="explicit", **extra):
    return {"type": "string", "unit": "1", "write": write, "requirement": requirement, **extra}


@pytest.mark.parametrize(
    "selector,modes,operation,mapping,expected",
    [
        (
            "calc",
            ["area", "width", "length"],
            "ne",
            {"a": "area", "w": "width", "l": "length"},
            [{"w", "l"}, {"a", "l"}, {"a", "w"}],
        ),
        (
            "calc",
            ["Resistance", "Length"],
            "eq",
            {"r": "Length", "segL": "Resistance"},
            [{"segL"}, {"r"}],
        ),
    ],
)
def test_calculated_mode_truth_table(selector, modes, operation, mapping, expected):
    params = {selector: parameter(domain={"kind": "set", "values": modes})}
    for name, selected in mapping.items():
        expr = {"op": operation, "left": {"param": selector}, "right": selected}
        params[name] = parameter("conditional", "conditional", write_when=expr, required_when=expr)
    result, status = rows({"parameters": params})
    assert status == "ok"
    for result_row, active in zip(result, expected):
        assert set(result_row["writable"]) == active | {selector}
        assert set(result_row["explicit_required"]) == active | {selector}
        assert set(result_row["inactive_conditional_inputs"]) == set(mapping) - active


def test_unbounded_selector_is_not_enumerated_or_guessed():
    expr = {"op": "eq", "left": {"param": "n"}, "right": 1}
    params = {
        "n": parameter(domain={"kind": "interval", "min": 0, "max": 3}),
        "w": parameter("conditional", write_when=expr),
    }
    result, status = rows({"parameters": params})
    assert status == "incomplete" and result[0]["selectors"] == ["n"]
