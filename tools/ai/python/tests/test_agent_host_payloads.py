"""Bridge payload bounds, isolation and lifetime survive contract relocation."""

import json

import pytest

from sico.core.contracts import BoundContext
from sico.core.host_contracts import digest
from sico.transport.framing import ProtocolError
from sico.transport.host_payload_contract import PAYLOAD_LIMIT, capture_payload
from sico.transport.host_payloads import HostPayloads


@pytest.fixture
def payload():
    context = BoundContext("instance", "generation", "input", {"valid": True})
    return dict(message=dict(kind="submit", id="input", text="Ask", context=context.record()),
                provider_config={"model": "example"}, environment={"KEY": "memory-only"})


@pytest.mark.parametrize("changes,error", [
    ({"unexpected": True}, "Invalid service message fields"),
    ({"message": {}}, "Invalid host payload"),
    ({"provider_config": []}, "Invalid host configuration"),
    ({"provider_config": {"handle": object()}}, "Invalid host payload"),
    ({"provider_config": {"value": float("nan")}}, "Invalid host payload"),
    ({"provider_config": {1: "one", "1": "two"}}, "Invalid JSON payload"),
    ({"environment": []}, "Invalid host environment"),
    ({"environment": {"": "value"}}, "Invalid host environment"),
    ({"environment": {"BAD=NAME": "value"}}, "Invalid host environment"),
    ({"environment": {"BAD\0NAME": "value"}}, "Invalid host environment"),
    ({"environment": {"KEY": "bad\0value"}}, "Invalid host environment"),
    ({"environment": {"KEY": 1}}, "Invalid host environment"),
    ({"attachment_text": None}, "Invalid host attachment"),
    ({"attachment_text": {}}, "Invalid host attachment"),
    ({"attachment_text": "bad\0text"}, "Invalid host attachment"),
])
def test_invalid_values_keep_protocol_error_categories(payload, changes, error):
    payload.update(changes)
    with pytest.raises(ProtocolError, match="^" + error + "$"):
        capture_payload(payload)


def test_circular_payload_is_a_protocol_error(payload):
    payload["provider_config"]["cycle"] = payload
    with pytest.raises(ProtocolError, match="^Invalid host payload$"):
        capture_payload(payload)


def test_submission_must_identify_the_captured_target(payload):
    payload["message"]["id"] = "another-target"
    with pytest.raises(ProtocolError, match="^Invalid host payload$"):
        capture_payload(payload)


def test_payload_byte_limit_is_inclusive(payload):
    payload["environment"] = {"X": ""}
    overhead = len(json.dumps(payload, ensure_ascii=True, allow_nan=False).encode("ascii"))
    payload["environment"]["X"] = "a" * (PAYLOAD_LIMIT - overhead)
    assert capture_payload(payload) == payload
    payload["environment"]["X"] += "a"
    with pytest.raises(ProtocolError, match="^Host payload exceeds limit$"):
        capture_payload(payload)


def test_environment_entry_limit_is_inclusive(payload):
    payload["environment"] = {"KEY_" + str(i): "value" for i in range(2048)}
    assert capture_payload(payload) == payload
    payload["environment"]["OVERFLOW"] = "value"
    with pytest.raises(ProtocolError, match="^Invalid host environment$"):
        capture_payload(payload)


def test_optional_attachment_obeys_existing_payload_bound_and_leaves_message_unchanged(payload):
    message = dict(payload["message"])
    payload["attachment_text"] = ""
    overhead = len(json.dumps(payload, ensure_ascii=True, allow_nan=False).encode("ascii"))
    payload["attachment_text"] = "a" * (PAYLOAD_LIMIT - overhead)
    accepted = capture_payload(payload)
    assert accepted == payload and accepted["message"] == message
    payload["attachment_text"] += "a"
    with pytest.raises(ProtocolError, match="^Host payload exceeds limit$"):
        capture_payload(payload)


def test_staged_payload_and_digest_are_detached_from_callers(payload):
    store = HostPayloads()
    reference = store.stage(object(), payload)
    payload["environment"]["KEY"] = "changed"
    first = store.read(reference["payload_id"])
    assert first["environment"]["KEY"] == "memory-only"
    assert reference["digest"] == digest(first)
    first["provider_config"]["model"] = "changed"
    assert store.read(reference["payload_id"])["provider_config"]["model"] == "example"


def test_capacity_and_owner_release_keep_other_payloads(payload):
    store = HostPayloads()
    owner, other = object(), object()
    owned = store.stage(owner, payload)
    others = [store.stage(other, payload) for _ in range(15)]
    with pytest.raises(ValueError, match="^Host payload capacity reached$"):
        store.stage(other, payload)
    store.release(owner)
    with pytest.raises(ValueError, match="^Host payload expired or detached$"):
        store.read(owned["payload_id"])
    assert all(store.read(row["payload_id"]) == payload for row in others)
    store.stage(owner, payload)
    store.release_all()
    with pytest.raises(ValueError, match="^Host payload expired or detached$"):
        store.read(others[0]["payload_id"])


def test_payloads_expire_at_120_seconds_and_capacity_is_reusable(payload):
    now = [0]
    store = HostPayloads(clock=lambda: now[0])
    references = [store.stage(object(), payload) for _ in range(16)]
    now[0] = 119.999
    assert store.read(references[0]["payload_id"]) == payload
    now[0] = 120
    with pytest.raises(ValueError, match="^Host payload expired or detached$"):
        store.read(references[0]["payload_id"])
    assert store.read(store.stage(object(), payload)["payload_id"]) == payload
