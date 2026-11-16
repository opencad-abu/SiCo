"""Phase 4: index-backed search filters, D4 not_indexed answers and offline-first reads."""

import pytest

from cadai.pdk_schema import PdkUnavailable
from cadai.pdk_session import PdkSession
from pdk_fixture import Bridge, raw
from test_pdk_enrichment import build_install
from test_pdk_preparation import context_with_pdk, finish


def parameter(name, value):
    return {"name": raw(name), "paramType": raw("string"), "type": raw("cellData"),
            "defValue": raw(value), "editable": raw("cdfgData->x->value"), "display": raw("t"),
            "callback": raw("error(\"must not execute\")"), "parseAsNumber": raw("yes"),
            "parseAsCEL": raw(None), "units": raw("lengthMetric")}


def patched(bridge, model_for):
    """Add a CDF `model` default and an auxiliary `dfm` parameter to every fixture device."""
    capture = bridge.capture
    names = ["model", "w", "dfm"]

    def wrapper(op, target):
        result = capture(op, target)
        if op == "categories":
            result["data"]["items"][0]["members"] = ["cell0", "cell1", "cell2",
                                                     "cell_extra", "cell_missing"]
        elif op == "device":
            items = result["data"]["cdf"]["parameters"]["items"]
            items.append(parameter("model", model_for(target["cell"])))
            items.append(parameter("dfm", "0"))
        elif op == "directory":
            for record in result["data"]["items"]:
                record["cdf_summary"]["names"] = list(names)
        return result

    return wrapper


def prepared(tmp_path, monkeypatch):
    root = build_install(tmp_path)
    bridge = Bridge()
    context_with_pdk(bridge, ("pdk_a",))
    bridge.ctx["libraries"][0]["resolved_path"] = str(root / "pdk_a")
    monkeypatch.setattr(bridge, "capture", patched(
        bridge, lambda cell: "BADMODEL" if cell == "cell1" else "GOODMODEL"))
    service = PdkSession(bridge=bridge, workspace=tmp_path, environment={})
    done = finish(service.preparation,
                  service.preparation.prepare({"library": "pdk_a", "choice_confirmed": True}))
    assert done["status"] == "ready"
    return bridge, service, done


def cells(service, **query):
    result = service.call("search_pdk_devices", {"library": "pdk_a", **query})
    return [item["target"]["cell"] for item in result["items"]]


def test_compat_search_retains_model_missing_devices_with_index_summary(tmp_path, monkeypatch):
    _, service, _ = prepared(tmp_path, monkeypatch)
    result = service.call("search_pdk_devices", {"library": "pdk_a"})
    assert [item["target"]["cell"] for item in result["items"]] == ["cell0", "cell1", "cell2"]
    item = result["items"][0]["device_index"]
    assert item["categories"] == ["mos"]
    assert item["model"] == "GOODMODEL" and item["model_status"] == "found"
    assert item["ranges"] == ["w"]
    assert item["tiers"] == {"interface": 2, "derived": 0, "auxiliary": 1}
    assert item["readiness"] == "partial"
    assert result["not_indexed_count"] == 0
    assert result["not_indexed"] == []
    assert result["device_index"]["digest"] == service.preparation.catalogs["pdk_a"]["digest"]


def test_search_filters_use_the_collected_device_index(tmp_path, monkeypatch):
    _, service, _ = prepared(tmp_path, monkeypatch)
    assert cells(service, category="mos") == ["cell0", "cell1", "cell2"]
    assert cells(service, category="resistors") == []
    assert cells(service, tier="interface", parameter="model") == ["cell0", "cell1", "cell2"]
    assert cells(service, tier="auxiliary") == ["cell0", "cell1", "cell2"]
    assert cells(service, tier="derived") == []
    assert cells(service, range="w") == ["cell0", "cell2"]
    assert cells(service, range="l") == []
    assert cells(service, readiness="partial") == ["cell0", "cell1", "cell2"]
    assert cells(service, readiness="ready_for_design") == []
    empty = service.call("search_pdk_devices", {"library": "pdk_a", "category": "resistors"})
    assert empty["items"] == [] and empty["status"] == "complete"


@pytest.mark.parametrize("cell,reason,action", [
    ("cell_extra", "no_symbol_view", "not_a_front_end_device"),
    ("cell_missing", "registry_stale", "refresh_pdk_collection"),
    ("unpublished", "not_published", "refresh_pdk_collection"),
])
def test_excluded_cell_answers_not_indexed_with_its_reason(tmp_path, monkeypatch, cell, reason, action):
    _, service, _ = prepared(tmp_path, monkeypatch)
    result = service.call("search_pdk_devices", {"library": "pdk_a", "cell": cell})
    assert result["status"] == "not_indexed" and result["reason"] == reason
    assert result["cell"] == cell and result["next_action"] == action
    assert result["excluded"] is (cell != "unpublished") and result["index_digest"]
    assert "items" not in result
    snapshot = result["context"]["snapshot_ref"]
    assert result["context"]["directory_digest"]
    repeated = service.call("search_pdk_devices", {
        "library": "pdk_a", "cell": cell, "snapshot_ref": snapshot})
    assert repeated == result
    service.store.snapshots.pop(snapshot)
    with pytest.raises(PdkUnavailable, match="evicted"):
        service.call("search_pdk_devices", {
            "library": "pdk_a", "cell": cell, "snapshot_ref": snapshot})


def test_device_index_filters_need_prepared_extracted_data():
    service = PdkSession(bridge=Bridge())
    with pytest.raises(PdkUnavailable, match="require a prepared workspace database"):
        service.call("search_pdk_devices", {"category": "mos"})


def test_device_reads_are_offline_first_with_the_interface_tier_default(tmp_path, monkeypatch):
    bridge, service, _ = prepared(tmp_path, monkeypatch)
    search = service.call("search_pdk_devices", {"library": "pdk_a"})
    ref = search["items"][0]["device_ref"]
    snapshot = search["context"]["snapshot_ref"]
    live = [call for call in bridge.calls if call[0] == "device"]
    detail = service.call("get_pdk_device", {"snapshot_ref": snapshot, "device_ref": ref})
    assert detail["cache_status"] == "persisted_database"
    assert [call for call in bridge.calls if call[0] == "device"] == live  # no live capture
    parameters = detail["device"]["parameters"]
    assert parameters["tier"] == "interface" and parameters["tier_applied"] is True
    assert parameters["count"] == 2 and parameters["all_count"] == 3
    assert [item["name"] for item in parameters["items"]] == ["w", "model"]
    assert detail["device"]["readiness"]["overall"] == "partial"
    assert detail["device"]["port_crosscheck"]["deck"] == ["d", "g", "s", "b"]
    full = service.call("get_pdk_device", {"snapshot_ref": snapshot, "device_ref": ref,
                                           "tier": "all"})
    assert full["cache_status"] == "persisted_database"
    names = [item["name"] for item in full["device"]["parameters"]["items"]]
    assert names == ["w", "model", "dfm"]


def test_live_fallback_reports_tier_unavailable(tmp_path, monkeypatch):
    bridge = Bridge()
    context_with_pdk(bridge, ("pdk_a",))
    monkeypatch.setattr(bridge, "capture", patched(bridge, lambda cell: "GOODMODEL"))
    service = PdkSession(bridge=bridge, workspace=tmp_path, environment={})
    done = finish(service.preparation,
                  service.preparation.prepare({"library": "pdk_a", "choice_confirmed": True}))
    assert done["status"] == "ready"
    search = service.call("search_pdk_devices", {"library": "pdk_a"})
    assert search["items"], "preparation should publish included devices"
    # Drop the published row so the read has to fall back to the live session.
    snapshot = search["context"]["snapshot_ref"]
    ref = search["items"][0]["device_ref"]
    service.store.snapshots[snapshot]["device_index"]["devices"] = {}
    detail = service.call("get_pdk_device", {"snapshot_ref": snapshot, "device_ref": ref})
    assert detail["cache_status"] == "live_session_capture"
    parameters = detail["device"]["parameters"]
    assert parameters["tier_unavailable"] is True and parameters["tier_applied"] is False
    assert parameters["count"] == 3


def test_revalidate_reports_persisted_and_observed_dependencies(tmp_path, monkeypatch):
    _, service, _ = prepared(tmp_path, monkeypatch)
    search = service.call("search_pdk_devices", {"library": "pdk_a"})
    ref = search["items"][0]["device_ref"]
    snapshot = search["context"]["snapshot_ref"]
    service.call("get_pdk_device", {"snapshot_ref": snapshot, "device_ref": ref})
    check = service.call("revalidate_pdk_bindings", {"snapshot_ref": snapshot, "device_refs": [ref]})
    assert check["status"] == "valid" and check["observation_source"] == "live_session_capture"
    assert check["cache_used_for_observation"] is False
    item = check["items"][0]
    assert item["expected_revision"] == item["observed_revision"]
    assert item["expected_dependency_digests"] == item["observed_dependency_digests"]
    assert check["provenance"]["collector_revision"]
    assert check["provenance"]["index_digest"] == service.preparation.catalogs["pdk_a"]["digest"]
