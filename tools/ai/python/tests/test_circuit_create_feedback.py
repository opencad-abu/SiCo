"""Failures preserve native evidence and direct callers to existing lifecycle tools."""

import copy
import json

import pytest

from cadai.circuit_create import call_create
from cadai.circuit_create_feedback import attach_creation_feedback

REQUEST = dict(prepare_ref="create-prepare:x", geometry_plan_digest="a" * 64, request_id="x")


@pytest.mark.parametrize("native,tool", [
    (dict(ok=False, code="prepare_not_found"), "prepare_circuit_creation"),
    (dict(ok=False, code="creation_preflight_failed"), "prepare_circuit_creation"),
    (dict(ok=False, stage="check", error="net OUT failed", circuit_ref="created:x",
          saved=False, view_created=True), "inspect_created_circuit"),
    (dict(ok=False, stage="saved_readback", error="readback", circuit_ref="created:x",
          saved=True, readback_verified=False), "inspect_created_circuit"),
])
def test_native_errors_have_action_without_losing_stage_or_objects(native, tool):
    original = copy.deepcopy(native)
    class Client:
        def call(self, *args):
            return True, dict(ok=True, value=json.dumps(native))

    ok, result = call_create("create_circuit_from_plan", REQUEST, Client())
    assert not ok
    assert {k: result[k] for k in native} == original
    assert result["next_action"]["tool"] == tool
    assert result["next_action"]["automatic_retry"] is False
    if native.get("circuit_ref"):
        assert result["next_action"]["retained_inputs"] == {"circuit_ref": "created:x"}
    assert native == original


def test_transport_unknown_preserves_original_id_and_does_not_dispatch_again():
    class Client:
        calls = 0
        def call(self, *args):
            self.calls += 1
            return False, dict(code="circuit_outcome_unknown", error="transport timeout")

    client = Client()
    ok, result = call_create("create_circuit_from_plan", REQUEST, client)
    assert not ok and client.calls == 1
    assert result["next_action"]["retained_inputs"] == REQUEST
    assert "get_circuit_operation" in result["next_action"]["message"]


def test_inspect_changed_uses_retained_reference():
    class Client:
        def call(self, *args):
            return True, dict(ok=True, value=json.dumps(dict(ok=False,
                             code="created_circuit_changed")))

    _, result = call_create("inspect_created_circuit", {"circuit_ref": "created:x"}, Client())
    assert result["next_action"]["retained_inputs"]["circuit_ref"] == "created:x"


def test_preparation_failure_reuses_request_identity_and_lists_required_plan_fields():
    args = dict(request_id="original", task_ref="task:x", binding_refs=["binding:x"])
    result = attach_creation_feedback(
        dict(ok=False, code="creation_preflight_failed"),
        "prepare_circuit_creation", args, ok=False,
    )
    action = result["next_action"]
    assert action["retained_inputs"] == args
    assert {"preview_digest", "geometry_plan_digest", "spec", "layout"} <= set(
        action["input_fields"])
    assert "retained" in action["message"]


def test_missing_preparation_keeps_original_create_id_for_status_tracking():
    result = attach_creation_feedback(
        dict(ok=False, code="prepare_not_found"), "create_circuit_from_plan", REQUEST, ok=False)
    assert result["next_action"]["retained_inputs"] == REQUEST
