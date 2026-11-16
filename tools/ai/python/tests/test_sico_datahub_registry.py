"""The JSON contract registry is the single source for names and actions."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import sico_datahub_wire as wire
from sico_datahub import contracts
from sico_datahub.cad_pilot import request
from sico_datahub.errors import HubError
from sico_datahub.registry import Registry, contract_root, default_registry

ROOT = Path(__file__).resolve().parents[4]


def test_shipped_registry_matches_generated_constants():
    registry = default_registry()
    assert registry.names() == contracts.CONTRACTS
    assert len(registry.names()) == 13
    assert contracts.ACTIONS["sico.cad.object_ref.v1"] == ()
    assert contracts.SCHEMAS == {"sico.cad.object_ref.v1": "cad.object_ref.v1"}
    assert contracts.ACTIONS["sico.calibre.lvs.v1"] == (
        "check", "export", "probe", "result", "status", "submit"
    )
    assert contracts.ACTIONS["sico.cad.layout_export.v1"] == ("visible_oasis",)
    assert contracts.RESPONSE_SCHEMAS["sico.cad.layout_export.v1"] == {
        "visible_oasis": "cad.layout_export.v1"
    }
    assert contracts.ACTIONS["sico.xcelium.run.v1"] == (
        "probe", "run", "status", "stop"
    )
    assert set(contracts.ERROR_CODES) == set(wire.ERROR_CODES)
    assert contracts.LEGACY_ERRORS == json.loads(
        (contract_root().parent / "legacy_errors.json").read_text()
    )
    for name, contract in registry.contracts.items():
        assert contract.version == contracts.VERSIONS[name]
        assert tuple(sorted(contract.actions)) == contracts.ACTIONS[name]
        assert {
            action: spec.capability
            for action, spec in contract.actions.items()
        } == contracts.CAPABILITIES[name]
        assert {
            action: spec.response_schema
            for action, spec in contract.actions.items()
        } == contracts.RESPONSE_SCHEMAS[name]


def test_contract_generator_check_is_clean():
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/common/datahub/generate_contracts.py"),
            "--check",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_registry_distinguishes_unknown_contract_version_and_action():
    registry = default_registry()
    with pytest.raises(HubError) as unknown:
        registry.get("sico.cad.missing.v1", 1)
    assert unknown.value.code == "unsupported_contract"
    with pytest.raises(HubError) as version:
        registry.get("sico.cad.objects.v1", 2)
    assert version.value.code == "version_mismatch"
    with pytest.raises(HubError) as action:
        registry.action("sico.cad.objects.v1", 1, "missing", {"aiSelectionList"})
    assert action.value.code == "unsupported_contract"


def test_missing_capability_is_rejected_before_dispatch():
    envelope = request(
        "sico.cad.objects.v1",
        "list",
        "o1",
        {
            "window_ref": "wctx_fixture_1",
            "kind": "all",
            "query": "",
            "offset": 0,
            "limit": 50,
        },
        registry=default_registry(),
    )
    with pytest.raises(HubError) as failure:
        default_registry().validate_request(envelope, capabilities={"aiWindowList"})
    assert failure.value.code == "capability_not_available"
    assert failure.value.details == {"capability": "aiSelectionList"}
    default_registry().validate_request(envelope, capabilities={"aiSelectionList"})


def test_request_fields_must_be_explicit_and_bounded():
    registry = default_registry()
    base = {
        "window_ref": "wctx_fixture_1",
        "kind": "all",
        "query": "",
        "offset": 0,
        "limit": 50,
    }
    with pytest.raises(HubError, match="payload fields"):
        request("sico.cad.objects.v1", "list", "o1", {
            key: value for key, value in base.items() if key != "query"
        }, registry=registry)
    with pytest.raises(HubError, match="payload fields"):
        request(
            "sico.cad.objects.v1",
            "list",
            "o1",
            {**base, "unexpected": ""},
            registry=registry,
        )
    for change in ({"limit": 51}, {"limit": True}, {"offset": -1}, {"offset": False}):
        with pytest.raises(HubError, match="outside the contract"):
            request(
                "sico.cad.objects.v1",
                "list",
                "o1",
                {**base, **change},
                registry=registry,
            )
    built = request("sico.cad.objects.v1", "list", "o1", base, registry=registry)
    assert built.to_dict()["payload"]["query"] == ""


def test_request_envelopes_cannot_carry_response_pages(tmp_path):
    registry = default_registry()
    envelope = request(
        "sico.cad.objects.v1",
        "list",
        "o1",
        {
            "window_ref": "wctx_fixture_1",
            "kind": "all",
            "query": "",
            "offset": 0,
            "limit": 50,
        },
        registry=registry,
    )
    from sico_datahub.envelope import Page

    paged = envelope.__class__(
        contract=envelope.contract,
        action=envelope.action,
        id=envelope.id,
        payload=envelope.payload,
        page=Page(0, 50, 1, None, False),
    )
    with pytest.raises(HubError, match="cannot carry a page"):
        registry.validate_request(paged)


def test_contract_files_and_actionless_schema_are_checked(tmp_path):
    source = contract_root() / "sico.cad.object_ref.v1.json"
    value = json.loads(source.read_text())
    path = tmp_path / source.name
    path.write_text(json.dumps(value))
    registry = Registry.from_directory(tmp_path)
    contract = registry.get("sico.cad.object_ref.v1", 1)
    assert contract.schema == "cad.object_ref.v1" and not contract.actions
    value["name"] = "sico.cad.other.v1"
    path.write_text(json.dumps(value))
    with pytest.raises(HubError, match="filename/name"):
        Registry.from_directory(tmp_path)
