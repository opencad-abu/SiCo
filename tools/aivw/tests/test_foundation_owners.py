"""Public behavior at the extracted AIVW foundation boundaries."""

import hashlib
import json

import pytest

from aivworkbench import contract, manifest
from aivworkbench.contract import (
    ArtifactLocator,
    BoundedContext,
    ContextPage,
    ErrorCode,
    ProtocolError,
    redact,
)
from aivworkbench.manifest_resolution import resolve_indexed_artifact
from aivworkbench.metric_results import LDOEvaluation, MetricObservation
from aivworkbench.ldo_metrics import evaluate_metrics


def test_compatibility_exports_share_the_authoritative_objects():
    assert contract.ArtifactLocator is ArtifactLocator
    assert contract.BoundedContext is BoundedContext
    assert contract.ContextPage is ContextPage
    assert contract.redact is redact
    assert manifest.resolve_indexed_artifact is resolve_indexed_artifact
    result = evaluate_metrics([{"name": "vout", "kind": "numeric"}], {"vout": 1}, {"vout": 1})
    assert type(result) is LDOEvaluation
    assert type(result.observations[0]) is MetricObservation


def test_context_and_protocol_use_one_redaction_policy():
    value = {
        "token_budget": 12, "tokens": "credential", "password": "secret",
        "secret_reference": {"source": "environment", "name": "CAD_MODEL_KEY"},
        "log": "Bearer sk-abcdefghijklmnop",
    }
    value["cycle"] = value
    expected = {
        "token_budget": 12, "tokens": "[REDACTED]", "password": "[REDACTED]",
        "secret_reference": {"source": "environment", "name": "CAD_MODEL_KEY"},
        "log": "Bearer [REDACTED]", "cycle": "[REDACTED_CYCLE]",
    }
    assert redact(value) == expected
    bounded = BoundedContext(value)
    assert bounded.to_dict()["context"] == expected
    assert value["password"] == "secret"
    assert contract.redact_text("Bearer sk-abcdefghijklmnop") == "Bearer [REDACTED]"
    assert contract.redact(object()) == "[REDACTED_UNSUPPORTED]"


@pytest.mark.parametrize("cap", [2, 3, 16, 64, 512])
def test_page_accounting_survives_redaction_and_small_caps(cap):
    bounded = BoundedContext({"z": "汉" * 80, "a": {"token": "secret"}}, max_bytes=cap)
    encoded = json.dumps(bounded.value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    assert bounded.digest == hashlib.sha256(encoded.encode()).hexdigest()
    assert bounded.bytes_used <= cap
    for page in bounded.pages(page_size=1):
        assert page.bytes_used <= cap
        assert page.transport_bytes <= bounded.page_transport_cap
        copied = page.to_dict()
        copied["items"].clear()
        assert page.to_dict()["bytes_used"] == page.bytes_used


def test_locator_secret_and_path_checks_remain_independent(tmp_path):
    with pytest.raises(ProtocolError) as secret:
        ArtifactLocator("logs/access_token.txt")
    assert secret.value.code == ErrorCode.PATH_DENIED.value
    target = tmp_path / "plain.json"
    target.write_text("{}")
    (tmp_path / "alias").symlink_to(target)
    with pytest.raises(ProtocolError) as alias:
        ArtifactLocator("alias").resolve(tmp_path, must_exist=True)
    assert alias.value.code == ErrorCode.SYMLINK_ESCAPE.value
