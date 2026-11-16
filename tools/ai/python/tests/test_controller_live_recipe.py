"""Recipe result binding preserves the saved target and cancellation dependency."""

from __future__ import annotations

import copy
import threading

import pytest

from cadai.aivw_tool import AivwArgumentError, AivwExecutionError
from cadai.controller_live import run_live_recipe, snapshot_binding_from_aivw


@pytest.mark.parametrize(
    "condition,expected_code",
    [("match", None), ("missing_snapshot", "snapshot_generation_unavailable"),
     ("missing_event", "snapshot_binding_unavailable"),
     ("wrong_target", "snapshot_binding_mismatch"),
     ("wrong_view", "snapshot_binding_mismatch")],
)
def test_recipe_binds_only_matching_authoritative_snapshot(tmp_path, condition, expected_code):
    target = {"library": "work", "cell": "top", "module": "top"}
    view = {"library": "work", "cell": "top", "view_name": "schematic",
            "oa_view_type": "schematic",
            "oa_view_type_source": "platform_api", "kind": "schematic"}
    active = {"source_generation": "save-event-12", "target": target, "view_identity": view}
    outputs = {"source_generation": "a" * 64, "target": dict(target), "view_identity": dict(view)}
    if condition == "missing_event":
        active.pop("view_identity")
    if condition == "wrong_target":
        outputs["target"]["cell"] = "other"
    if condition == "wrong_view":
        outputs["view_identity"]["view_name"] = "other"
    gates = {"snapshot": {"executor": "virtuoso.snapshot", "status": "PASS", "outputs": outputs}}
    result = {"aivw": {"status": "PASS", "details": {"gate_results": gates}}}
    if condition == "missing_snapshot":
        gates.clear()
    config = {"recipe_id": "recipe", "through": "validation", "profile": "amsverify"}
    original = copy.deepcopy((config, active))
    cancel = threading.Event()
    capability = object()
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return result

    returned = run_live_recipe(
        config, active, run_aivw_recipe=run, capability=capability,
        workspace=tmp_path, timeout=7200, cancel_event=cancel,
        snapshot_binding=snapshot_binding_from_aivw,
    )
    assert returned is result
    assert calls == [({"reference": "recipe", "through": "validation", "profile": "amsverify",
                       "timeout": 3600.0},
                      {"client": capability, "workspace": tmp_path, "cancel_event": cancel})]
    assert (config, active) == original
    assert result["requested_source_generation"] == "save-event-12"
    if expected_code is None:
        assert result["status"] == "PASS"
        assert result["validated_source_generation"] == "a" * 64
        assert result["snapshot_source_generation"] == "a" * 64
    else:
        assert result["status"] == "BLOCKED_INPUT" and result["code"] == expected_code
        assert "validated_source_generation" not in result


@pytest.mark.parametrize("invalid_config", [True, False])
def test_invalid_recipe_input_or_result_is_not_retried(tmp_path, invalid_config):
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return {"aivw": {"status": None}}

    config = {"recipe_id": None if invalid_config else "recipe", "through": "validation"}
    with pytest.raises(AivwArgumentError if invalid_config else AivwExecutionError):
        run_live_recipe(
            config, {"source_generation": "save-1"}, run_aivw_recipe=run,
            capability=object(), workspace=tmp_path, timeout=30,
            cancel_event=threading.Event(), snapshot_binding=snapshot_binding_from_aivw,
        )
    assert len(calls) == (0 if invalid_config else 1)
