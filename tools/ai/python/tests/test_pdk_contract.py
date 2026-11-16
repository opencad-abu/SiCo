"""Machine-readable schema artifacts and successful public result validation."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from cadai.pdk_contract import OUTPUT
from cadai.pdk_schema import PDK_TOOLS
from cadai.pdk_session import PdkSession
from pdk_fixture import Bridge

DOCS = Path(__file__).resolve().parents[2] / "docs"


def test_published_pdk_schemas_match_source():
    assert json.loads((DOCS / "pdk-session-tools.schema.json").read_text()) == PDK_TOOLS
    assert json.loads((DOCS / "pdk-session-response.schema.json").read_text()) == OUTPUT


def test_pdk_response_contract():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator.check_schema(OUTPUT)
    validator = jsonschema.Draft202012Validator(OUTPUT)
    service = PdkSession(bridge=Bridge())
    search = service.call("search_pdk_devices", {})
    validator.validate(search)
    snap, ref = search["context"]["snapshot_ref"], search["items"][0]["device_ref"]
    validator.validate(service.call("get_pdk_device", {"snapshot_ref": snap, "device_ref": ref}))
    validator.validate(service.call("get_pdk_device", {"snapshot_ref": snap, "device_ref": ref, "sections": ["ports"]}))
    validator.validate(service.call("revalidate_pdk_bindings", {"snapshot_ref": snap, "device_refs": [ref]}))


def test_prepared_index_responses_match_schema(tmp_path, monkeypatch):
    """Phase 4 responses (index filters, not_indexed, offline detail) satisfy the contract."""
    jsonschema = pytest.importorskip("jsonschema")
    from test_pdk_session_index import prepared

    validator = jsonschema.Draft202012Validator(OUTPUT)
    _, service, _ = prepared(tmp_path, monkeypatch)
    search = service.call("search_pdk_devices", {"library": "pdk_a", "category": "mos"})
    validator.validate(search)
    excluded = service.call("search_pdk_devices", {"library": "pdk_a", "cell": "cell1"})
    validator.validate(excluded)
    ref = search["items"][0]["device_ref"]
    snap = search["context"]["snapshot_ref"]
    interface = service.call("get_pdk_device", {"snapshot_ref": snap, "device_ref": ref})
    validator.validate(interface)
    full = service.call("get_pdk_device", {"snapshot_ref": snap, "device_ref": ref, "tier": "all"})
    validator.validate(full)
    missing_raw = deepcopy(full)
    del missing_raw["device"]["parameters"]["items"][0]["raw"]
    assert not validator.is_valid(missing_raw)
    unmarked_projection = deepcopy(interface)
    del unmarked_projection["device"]["parameters"]["projection"]
    assert not validator.is_valid(unmarked_projection)


def test_actual_pdk_example_pages_match_schema():
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(OUTPUT)
    for path in (DOCS / "examples").glob("pdk-session-*.json"):
        data = json.loads(path.read_text())
        for response in data["responses"]:
            validator.validate(response)
