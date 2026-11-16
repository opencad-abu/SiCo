from __future__ import annotations

from types import SimpleNamespace

import pytest

import sicoruntime


def test_runtime_report_exposes_the_selected_interpreter_and_tls():
    result = sicoruntime.report({})
    assert result["python"].startswith("/")
    assert result["version"]
    assert result["openssl"]
    assert "errors" in result


def test_missing_tls_trust_store_is_reported_before_model_traffic(monkeypatch):
    monkeypatch.setattr(
        sicoruntime,
        "_default_verify_paths",
        lambda: SimpleNamespace(cafile=None, capath=None),
    )
    result = sicoruntime.report({})
    assert result["ok"] is False
    assert any("CA" in message for message in result["errors"])
    with pytest.raises(RuntimeError, match="TLS runtime"):
        sicoruntime.validate({})
