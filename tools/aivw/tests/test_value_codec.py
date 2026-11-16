"""Contract value codec behavior at the core boundary."""

from __future__ import annotations

from aivworkbench.contract.json_values import ensure_json, freeze, redact, thaw


def test_value_codec_preserves_budget_and_freezes_redacted_payload() -> None:
    raw = {
        "tokens": 12,
        "secret": "sk-test_abcdefghijklmnop",
        "nested": [{"password": "hidden", "ok": True}],
    }
    value = freeze(redact(raw))
    assert value["tokens"] == 12
    assert value["secret"] == "[REDACTED]"
    assert value["nested"][0]["password"] == "[REDACTED]"
    assert thaw(value) == {
        "tokens": 12,
        "secret": "[REDACTED]",
        "nested": [{"password": "[REDACTED]", "ok": True}],
    }
    try:
        value["new"] = 1
    except TypeError:
        pass
    else:
        raise AssertionError("frozen contract mapping accepted mutation")


def test_value_codec_rejects_non_finite_json() -> None:
    try:
        ensure_json({"value": float("nan")})
    except ValueError as exc:
        assert "non-finite" in str(exc)
    else:
        raise AssertionError("non-finite contract value was accepted")
