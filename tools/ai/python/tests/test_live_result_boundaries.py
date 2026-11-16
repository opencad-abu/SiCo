"""Live result metadata is bounded, sanitized and verdict-free."""

from __future__ import annotations

import pytest

from cadai import live_result
from cadai.live_protocol import LiveModelProtocolError


def test_bounded_text_accepts_only_strings():
    assert live_result._bounded_text(None) is None
    assert live_result._bounded_text(3) is None
    assert live_result._bounded_text("ab", limit=1) == "a"


def test_error_text_redacts_common_credentials_and_bounds_length():
    text = live_result._sanitize_error_text(
        "Bearer abc api_key=SECRETKEY password: SECRETPASS token=SECRETTOKEN "
        "sk_live_123456789012345")
    for secret in ("abc", "SECRETKEY", "SECRETPASS", "SECRETTOKEN", "sk_live"):
        assert secret not in text
    assert live_result._sanitize_error_text("x" * 600, limit=4) == "xxxx"
    assert live_result._sanitize_error_text(RuntimeError("secret=value")) == (
        "secret=<redacted>")


def test_bounded_result_keeps_only_reviewed_metadata():
    value = live_result._bounded_result({
        "status": "saved",
        "run_id": "run",
        "source_generation": "source",
        "requested_source_generation": "requested",
        "snapshot_source_generation": "snapshot",
        "validated_source_generation": "validated",
        "artifacts": ["a", 2, "b"],
        "code": "ok",
        "message": "done",
        "ignored": "value",
    })
    assert value == {
        "result_status": "saved",
        "run_id": "run",
        "source_generation": "source",
        "requested_source_generation": "requested",
        "snapshot_source_generation": "snapshot",
        "validated_source_generation": "validated",
        "artifacts": ["a", "b"],
        "code": "ok",
        "message": "done",
    }


def test_bounded_result_rejects_verdict_and_oversized_metadata():
    with pytest.raises(LiveModelProtocolError, match="verdict"):
        live_result._bounded_result({"verdict": "completed"})
    with pytest.raises(LiveModelProtocolError, match="too large"):
        live_result._bounded_result({"artifacts": ["x" * 512] * 32,
                                     "message": "y" * 512})


def test_bounded_result_redacts_nested_and_opaque_values():
    value = live_result._bounded_result({
        "status": "token=secret",
        "message": "Bearer abc",
        "code": "sk_live_123456789012345",
    })
    assert value["result_status"] == "token=<redacted>"
    assert value["message"] == "Bearer <redacted>"
    assert value["code"] == "<redacted>"
