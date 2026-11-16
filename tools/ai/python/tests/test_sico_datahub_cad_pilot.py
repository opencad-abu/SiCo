"""CAD pilots map existing validator-owned DTOs without changing their shape."""

import ast
import json
from pathlib import Path

import pytest
from sico.core.window_refs import normalize_window_list
from sico_datahub.cad_pilot import (
    PILOT_ACTIONS,
    from_legacy,
    request,
    to_legacy,
    validate_object_reference,
)
from sico_datahub.codec import decode, encode
from sico_datahub.envelope import Envelope, Error
from sico_datahub.errors import HubError
from sico_datahub.registry import default_registry

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).with_name("fixtures") / "sico_datahub"
CASES = (
    ("selection.json", "sico.cad.selection.v1", "inspect"),
    ("objects.json", "sico.cad.objects.v1", "list"),
    ("windows.json", "sico.cad.windows.v1", "list"),
    ("highlight.json", "sico.cad.highlight.v1", "highlight"),
)


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("filename, contract, action", CASES)
def test_legacy_dto_roundtrips_through_the_hub(filename, contract, action):
    legacy = _fixture(filename)
    envelope = from_legacy(contract, action, "request-1", legacy)
    assert decode(encode(envelope)) == envelope
    assert to_legacy(envelope) == legacy


@pytest.mark.parametrize(
    "action, filename",
    [
        ("selection", "selection.json"),
        ("objects", "objects.json"),
        ("windows", "windows.json"),
    ],
)
def test_ask_contract_shares_the_same_cad_dtos(action, filename):
    legacy = _fixture(filename)
    envelope = from_legacy("sico.ask.objects.v1", action, "ask-1", legacy)
    assert envelope.contract == "sico.ask.objects.v1"
    assert to_legacy(envelope) == legacy


def test_window_pilot_preserves_non_capturable_rows_but_validator_skips_them():
    legacy = _fixture("windows.json")
    envelope = from_legacy("sico.cad.windows.v1", "list", "w1", legacy)
    assert len(envelope.payload["windows"]) == 2
    assert len(normalize_window_list(legacy)["windows"]) == 1
    assert to_legacy(envelope)["windows"] == legacy["windows"]


def test_object_reference_is_a_pure_data_contract():
    value = _fixture("object_ref.json")
    normalized = validate_object_reference(value)
    assert normalized["schema"] == "cad.object_ref.v1"
    assert normalized["master"] == value["master"]
    contract = default_registry().get("sico.cad.object_ref.v1", 1)
    assert contract.schema == "cad.object_ref.v1"
    assert not contract.actions
    with pytest.raises(ValueError):
        validate_object_reference({**value, "schema": "cad.object_ref.v2"})


def test_legacy_error_codes_are_mapped_and_preserved_for_roundtrip():
    legacy = {
        "ok": False,
        "code": "unsupported_object",
        "message": "This object kind cannot be located",
    }
    envelope = from_legacy("sico.cad.highlight.v1", "highlight", "e1", legacy)
    assert envelope.status == "error"
    assert envelope.error.code == "invalid_arguments"
    assert envelope.error.details == {"legacy_code": "unsupported_object"}
    assert to_legacy(envelope) == legacy
    stable = {
        "ok": False,
        "code": "stale_context",
        "message": "The selected window target changed",
    }
    assert to_legacy(
        from_legacy("sico.cad.highlight.v1", "highlight", "e2", stable)
    ) == stable


def test_unknown_outcome_cannot_be_silently_replayed_as_legacy_dto():
    envelope = Envelope(
        contract="sico.cad.highlight.v1",
        action="highlight",
        id="u1",
        status="unknown",
        error=Error("timeout", "operation result is unknown"),
    )
    with pytest.raises(HubError) as failure:
        to_legacy(envelope)
    assert failure.value.code == "timeout"
    assert not envelope.may_retry(write=True)


def test_invalid_legacy_dto_is_still_rejected_by_the_existing_validator():
    value = _fixture("objects.json")
    value["schema"] = "cad.cv.objects.v2"
    with pytest.raises(ValueError, match="schema"):
        from_legacy("sico.cad.objects.v1", "list", "bad", value)


def test_ask_requests_keep_empty_query_as_an_explicit_field():
    payloads = _fixture("ask_requests.json")
    for action, payload in payloads.items():
        envelope = request("sico.ask.objects.v1", action, action, payload)
        encoded = json.loads(encode(envelope))
        assert encoded["payload"] == payload
    assert payloads["objects"]["query"] == ""


def test_all_pilot_actions_are_registered_and_six_pilot_contracts_are_frozen():
    registry = default_registry()
    assert len(registry.names()) == 13
    for contract, action in PILOT_ACTIONS:
        spec = registry.action(contract, 1, action)
        assert spec[1].response_schema
    assert {contract for contract, _action in PILOT_ACTIONS} == {
        "sico.ask.objects.v1",
        "sico.cad.highlight.v1",
        "sico.cad.objects.v1",
        "sico.cad.selection.v1",
        "sico.cad.windows.v1",
    }


def test_hub_imports_exclude_desktop_editor_and_workbench_layers():
    package = ROOT / "tools/common/python/sico_datahub"
    forbidden = ("PyQt5", "oa", "sico_ui", "sico.editor", "sico.workbench")
    for path in (package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        ]
        modules.extend(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(
            module == name or module.startswith(name + ".")
            for module in modules
            for name in forbidden
        ), path
