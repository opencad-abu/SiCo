"""Directory semantics exercised against real evaluators, with no circuit-specific code."""

import math
import re
from copy import deepcopy

import pytest
from measurement_catalog_fixture import task
from test_circuit_rc_gui import mcp_call
from test_measurement_catalog import IDS, Offline, detail

from cadai.circuit_spec_schema import validate
from cadai.mcp import McpServer
from cadai.result_tools import ResultStore


def run(path, capability_id, args):
    cap = detail(capability_id)["capability"]
    validate(args["recipes"][0], cap["recipe_schema"])
    report = mcp_call(McpServer(Offline(), workspace=path), cap["tool_name"], args)
    return cap, report


def replace_wave(path, args, key, *, prefix="ac_waveform", **fields):
    store = ResultStore(path)
    wave = store.get(args[key])
    wave.update(fields)
    args[key], _ = store.put(prefix, wave)


def documented_failure(capability, row):
    for failure in capability["failures"]["row_patterns"]:
        pattern = re.escape(failure["reason_pattern"])
        pattern = pattern.replace(r"\{role\}", "(?:input|output|signal)")
        pattern = pattern.replace(r"\{status\}", "(?:scalar|complex|family|missing|error)")
        if row["status"] == failure["status"] and re.fullmatch(pattern, row["reason"]):
            assert row["value"] is None
            return
    pytest.fail("Undocumented row failure: " + repr(row))


@pytest.mark.parametrize("capability_id", IDS)
def test_concrete_quantity_paths_match_current_recipe_or_tool_schema(capability_id):
    cap = detail(capability_id)["capability"]
    for path, unit_rule in cap["quantities"].items():
        scope, *parts = path.split(".")
        schema = cap["recipe_schema"] if scope == "recipe" else cap["tool_input_schema"]
        for part in parts:
            schema = schema["properties"][part]
        assert set(schema["required"]) == {"value", "unit"} and unit_rule


@pytest.mark.parametrize("capability_id", IDS)
def test_out_of_range_has_documented_missing_reason(tmp_path, capability_id):
    kind, op = capability_id.split(".")
    args = task(tmp_path, kind, op)
    r = args["recipes"][0]
    if "at" in r:
        r["at"]["value"] = 2e7
    elif "window" in r:
        r["window"]["stop"]["value"] = 2e7
    else:
        r["input_event"]["window"]["stop"]["value"] = 2e7
    cap, report = run(tmp_path, capability_id, args)
    documented_failure(cap, report["rows"][0])
    assert report["rows"][0]["status"] == "missing"


def test_real_arrival_and_ac_strict_crossing_are_distinct(tmp_path):
    args = task(tmp_path, "single", "crossing")
    args["recipes"][0]["window"]["stop"]["value"] = 1
    cap, report = run(tmp_path, "single.crossing", args)
    assert report["rows"][0]["value"] == 1  # Arrival exactly at stop counts.
    assert "arrival_crossing" in {r["code"] for r in cap["rules"]}
    ac = task(tmp_path, "ac_response", "phase_margin")
    replace_wave(tmp_path, ac, "output_ref", samples=[[100, 2, 0], [1000, 1, 0]])
    cap, report = run(tmp_path, "ac_response.phase_margin", ac)
    documented_failure(cap, report["rows"][0])  # Endpoint unity has no two-sided evidence.
    assert report["rows"][0]["reason"] == "requested_crossing_not_found"
    assert "strict_crossing" in {r["code"] for r in cap["rules"]}


@pytest.mark.parametrize(
    "capability_id,case",
    [
        ("pair.gain_sample", "denominator"),
        ("pair.delay", "axis"),
        ("ac_transfer.magnitude", "denominator"),
        ("ac_signal.phase", "phase_floor"),
        ("ac_signal.magnitude_db", "zero"),
        ("ac_response.phase_margin", "plateau"),
        ("ac_response.phase_margin", "denominator"),
        ("ac_response.phase_margin", "phase_floor"),
        ("ac_response.bandwidth", "low_start"),
    ],
)
def test_documented_error_conditions(tmp_path, capability_id, case):
    kind, op = capability_id.split(".")
    args = task(tmp_path, kind, op)
    r = args["recipes"][0]
    if case == "denominator":
        (args if kind.startswith("ac_") else r)["denominator_floor"] = dict(value=5, unit="V")
    elif case == "phase_floor":
        r["magnitude_floor"]["value"] = 5
    elif case == "axis":
        for key in ("input_ref", "output_ref"):
            replace_wave(tmp_path, args, key, prefix="waveform", x_unit="Hz")
    else:
        level = 0 if case == "zero" else 1 if case == "plateau" else 0.1
        key = "waveform_ref" if kind == "ac_signal" else "output_ref"
        replace_wave(tmp_path, args, key, samples=[[100, level, 0], [1000, level, 0]])
    cap, report = run(tmp_path, capability_id, args)
    documented_failure(cap, report["rows"][0])
    assert report["rows"][0]["status"] == "error"


def test_phase_branch_and_reference_not_implicit_defaults(tmp_path):
    args = task(tmp_path, "ac_response", "phase_margin")
    _, original = run(tmp_path, "ac_response.phase_margin", args)
    args["recipes"][0]["phase_anchor_turns"] = -1
    _, negative = run(tmp_path, "ac_response.phase_margin", args)
    assert original["rows"][0]["value"] - negative["rows"][0]["value"] == 360
    assert negative["rows"][0]["value"] == -180  # No wrap or abs.
    args = task(tmp_path, "ac_transfer", "magnitude_db")
    _, first = run(tmp_path, "ac_transfer.magnitude_db", args)
    args["recipes"][0]["reference"]["value"] = 2
    _, second = run(tmp_path, "ac_transfer.magnitude_db", args)
    assert first["rows"][0]["value"] - second["rows"][0]["value"] == pytest.approx(
        20 * math.log10(2)
    )


def test_semantic_restrictions_are_additional_to_shape_schema(tmp_path):
    args = task(tmp_path, "ac_response", "bandwidth")
    args["recipes"][0]["direction"] = "rising"  # Shared tool enum admits it for phase_margin.
    cap, report = run(tmp_path, "ac_response.bandwidth", args)
    assert not report["ok"] and "falling" in report["message"]
    assert any(r["code"] == "lowpass_cutoff" for r in cap["rules"])
    assert "semantic" in cap["schema_policy"]


def test_extra_field_from_another_operation_refused(tmp_path):
    args = task(tmp_path, "single", "sample")
    wrong = task(tmp_path, "single", "mean")["recipes"][0]["window"]
    args["recipes"][0]["window"] = deepcopy(wrong)
    cap = detail("single.sample")["capability"]
    validate(args, cap["tool_input_schema"])  # Tool-wide union alone is insufficient.
    assert not mcp_call(McpServer(Offline(), workspace=tmp_path), cap["tool_name"], args)["ok"]
