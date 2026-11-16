"""The native marker gates PDK execution; retained statuses retain retry semantics."""

import json

import pytest

from cadai.circuit_create import call_create
from cadai.circuit_spec_schema import CircuitSpecError

ARGS = dict(prepare_ref="create-prepare:test", geometry_plan_digest="a" * 64, request_id="test")


class Client:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args))
        return True, {"value": json.dumps(next(self.replies))}


class Adapter:
    def __init__(self, fail=False):
        self.fail = fail
        self.checked = []

    def validate_execute(self, refs):
        self.checked.append(refs)
        if self.fail:
            raise CircuitSpecError("PDK binding changed")


MARKER = dict(ok=False, code="pdk_validation_required", pdk_guard=["pdk-adapter:test"])


def test_marker_is_validated_before_native_retry():
    client, adapter = Client([MARKER, {"ok": True}]), Adapter()
    assert call_create("create_circuit_from_plan", ARGS, client, pdk_bindings=adapter)[0]
    assert adapter.checked == [["pdk-adapter:test"]]
    assert len(client.calls) == 2
    assert client.calls[1][1]["code"].endswith('list("pdk-adapter:test"))')


@pytest.mark.parametrize("adapter", [None, Adapter(fail=True)])
def test_stale_or_restarted_python_process_cannot_execute_preparation(adapter):
    client = Client([MARKER])
    with pytest.raises(CircuitSpecError):
        call_create("create_circuit_from_plan", ARGS, client, pdk_bindings=adapter)
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "result",
    [{"ok": True, "stage": "schematic_saved"}, {"ok": False, "stage": "save", "error": "partial"}],
)
def test_success_or_partial_failure_replay_does_not_revalidate_or_repeat_write(result):
    client, adapter = Client([result]), Adapter(fail=True)
    actual = call_create("create_circuit_from_plan", ARGS, client, pdk_bindings=adapter)[1]
    assert {key: actual[key] for key in result} == result
    if not result["ok"]:
        assert actual["next_action"]["automatic_retry"] is False
        assert actual["next_action"]["retained_inputs"] == ARGS
    assert adapter.checked == [] and len(client.calls) == 1
