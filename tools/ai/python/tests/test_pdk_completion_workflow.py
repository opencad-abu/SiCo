"""Completion is a maintenance operation; design stays read only until publication."""

import pytest
from pdk_standard_fixture import package, update
from test_pdk_standard_completion import legacy

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_session import PdkSession


def test_old_collector_does_not_mark_missing_body_as_collected(tmp_path):
    current, bridge, _, _ = package(tmp_path)
    index = tmp_path / ".sico/ai/pdk-data/index.json"
    before = index.read_bytes()
    session = PdkSession(
        bridge=bridge, workspace=tmp_path, environment={"SICO_PDK_WORKFLOW": "generation"}
    )
    with pytest.raises(PdkUnavailable, match="Collector lacks body"):
        session.call(
            "collect_pdk_data",
            {"library": "synthetic", "device": "cell0", "revision": current.revision},
        )
    assert index.read_bytes() == before


def test_mcp_exposes_explicit_completion_and_read_does_not_capture(tmp_path, monkeypatch):
    current, bridge, _ = legacy(tmp_path, monkeypatch)
    session = PdkSession(
        bridge=bridge, workspace=tmp_path, environment={"SICO_PDK_WORKFLOW": "generation"}
    )
    # The same handler is used by the host adapter and the MCP server.
    from sico.adapters.circuit import NAMES

    from cadai.mcp_pdk_handler import dispatch_pdk

    assert "collect_pdk_data" in NAMES
    ok, result = dispatch_pdk(
        "collect_pdk_data",
        {"library": "synthetic", "device": "cell0", "revision": current.revision},
        session=session,
        bindings=None,
        client=None,
    )
    assert ok and result["collected"]
    calls = len(bridge.calls)
    ok, read = dispatch_pdk(
        "get_pdk_data",
        {"library": "synthetic", "device": "cell0", "section": "symbol"},
        session=session,
        bindings=None,
        client=None,
    )
    assert ok and read["status"] == "incomplete" and len(bridge.calls) == calls


def test_user_update_cannot_forge_collection_marker(tmp_path):
    current, _, _, _ = package(tmp_path)
    with pytest.raises(PdkUnavailable, match="Collection marker"):
        update(
            tmp_path,
            current,
            [
                {
                    "op": "add",
                    "path": "/x_geometry_collection",
                    "value": "sico-static-symbol-facts-v1",
                }
            ],
            file="cell0/symbol.json",
        )


def test_completion_dependency_survives_qualified_publication(tmp_path):
    from pdk_publication_fixture import acceptance_evidence, ready_candidate

    from cadai.pdk_standard.publication import require
    from cadai.pdk_standard.workspace import select

    current, bridge, _ = ready_candidate(tmp_path)
    bridge.data["database"]["geometry"].update(
        body_outline_status="complete", body_shapes=[], mosaic_count=0
    )
    session = PdkSession(
        bridge=bridge, workspace=tmp_path, environment={"SICO_PDK_WORKFLOW": "generation"}
    )
    result = session.call(
        "collect_pdk_data",
        {"library": "synthetic", "device": "cell0", "revision": current.revision},
    )
    assert result["acceptance_required"] is True
    assert result["next_action"] == "accept_pdk_data"
    completed = select(tmp_path, "synthetic", {})
    symbol = completed.document("cell0/symbol.json")
    assert "cell0_geometry_v3" in symbol["depends_on"]
    published = session.call(
        "publish_pdk_data", {"library": "synthetic", "revision": result["revision"],
                             "acceptance_evidence": acceptance_evidence(tmp_path),
                             "unfinished_options": ["iv"]}
    )
    assert published["status"] == "published"
    current = select(tmp_path, "synthetic", {})
    require(current)
    assert current.document("cell0/symbol.json") == symbol
    assert (
        current.manifest["dependencies"]["cell0_geometry_v3"]["method"]
        == "sico-symbol-geometry-json-v3"
    )
