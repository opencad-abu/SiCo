"""Live-model JSON rejection and target identity contracts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from live_model_fixture import assert_protocol_code, source_message, start_message

from cadai.live_model import (
    LIVE_PROTOCOL_VERSION,
    loads_strict,
    parse_live_message,
    parse_live_status,
)


def test_loads_strict_accepts_one_json_object() -> None:
    assert loads_strict(b'{"event":"live_model.status"}') == {
        "event": "live_model.status"
    }


@pytest.mark.parametrize(
    "raw, code",
    [
        ('{"event":"live_model.status","event":"live_model.stop"}', "invalid_json"),
        ('{"event":"live_model.status","value":NaN}', "invalid_json"),
        ('{"event":"live_model.status","value":Infinity}', "invalid_json"),
        ('["live_model.status"]', "invalid_message"),
        ('{"event":', "invalid_json"),
    ],
)
def test_loads_strict_rejects_ambiguous_or_non_finite_json(raw: str, code: str) -> None:
    assert_protocol_code(code, lambda: loads_strict(raw))


@pytest.mark.parametrize(
    "mutate, code",
    [
        (lambda value: value.update({"unexpected": True}), "unknown_field"),
        (lambda value: value.update({1: "not-text"}), "invalid_payload"),
        (lambda value: value.update({"token": "secret-value"}), "unknown_field"),
        (
            lambda value: value["target"].update({"api_key": "secret-value"}),
            "credential_forbidden",
        ),
        (lambda value: value["target"].update({"verdict": "PASS"}), "verdict_forbidden"),
        (lambda value: value.update({"debounce_ms": float("nan")}), "invalid_number"),
    ],
)
def test_start_protocol_rejects_unknown_non_text_secret_verdict_and_nan(
    mutate: Callable[[dict[str, Any]], None], code: str
) -> None:
    message = start_message()
    mutate(message)
    assert_protocol_code(code, lambda: parse_live_message(message))


def test_copy_boundary_rejects_secret_and_verdict_keys_in_nested_status_result() -> None:
    base = {
        "protocol_version": LIVE_PROTOCOL_VERSION,
        "event": "live_model.status",
        "session_id": "live-test",
        "state": "ERROR",
        "publication": "DISABLED",
        "last_sequence": 1,
    }
    for key in ("access_token", "provider_verdict"):
        status = dict(base)
        status["result"] = {"metadata": {key: "must-not-cross"}}
        expected = "credential_forbidden" if key == "access_token" else "verdict_forbidden"
        assert_protocol_code(expected, lambda status=status: parse_live_status(status))


def test_target_view_binding_and_schematic_source_are_required() -> None:
    mismatch = source_message(1)
    mismatch["view_identity"]["cell"] = "OTHER"
    assert_protocol_code("identity_mismatch", lambda: parse_live_message(mismatch))

    symbol = source_message(1)
    symbol["view_identity"].update(
        {
            "view_name": "symbol", "oa_view_type": "symbol",
            "oa_view_type_source": "platform_api", "kind": "symbol",
        }
    )
    symbol["changed_kind"] = "symbol"
    assert_protocol_code("invalid_view", lambda: parse_live_message(symbol))
