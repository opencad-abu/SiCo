"""Bundled base libraries serve standard data without project collection.

The bundled standard packages (``tools/ai/share/pdk-data``: an index plus one
published package per library) drive the offline catalog and the workspace
baseline install; the legacy detail read follows the live session, and the
retired capture snapshot is never consulted.
"""
import pytest

from cadai import pdk_builtin
from cadai.pdk_session import PdkSession
from cadai.pdk_schema import PdkUnavailable
from cadai.pdk_standard.workspace import select
from pdk_fixture import Bridge
from test_pdk_preparation import context_with_pdk


@pytest.mark.parametrize("library,cell,count", [("analogLib", "vdc", 182), ("basic", "ipin", 82)])
def test_published_builtin_serves_the_v2_baseline_without_collection(
        tmp_path, library, cell, count):
    bridge = Bridge()
    context_with_pdk(bridge, (library, "pdk_a", "pdk_b"))
    session = PdkSession(bridge=bridge, workspace=tmp_path, environment={})
    session.preparation.choice_validator = lambda selected, _info: selected == library
    native = bridge.capture
    scans = []

    def record(op, target):
        scans.append(op)
        return native(op, target)

    bridge.capture = record
    for installation in ("cadence1", "cadence2"):
        path = tmp_path / installation / library
        path.mkdir(parents=True)
        bridge.ctx["libraries"][0]["resolved_path"] = str(path)
        for refresh in (False, True):
            del scans[:]
            ready = session.call("prepare_pdk_data", {"library": library, "refresh": refresh})
            assert ready["status"] == "ready"
            assert ready["source"] in {"builtin", "published_standard"}
            if ready["source"] == "builtin":
                assert ready["coverage"]["device_count"] == count
            else:
                assert ready["device_count"] == 6
            assert ready["design_ready"] is True
            session.preparation.selected = library
            session.preparation.selected_scope = session.preparation.selection_info["selection_ref"]
            assert not session.preparation.jobs
            assert "device" not in scans, "catalog/prepare must not scan devices"
            query = session.call("search_pdk_devices", {"library": library, "cell": cell})
            assert query["directory_source"] in {"builtin", "published_standard"}
            assert "device" not in scans, "catalog search must not scan devices"
            assert query["items"] and query["items"][0]["target"]["cell"] == cell
            answer = session.call("get_pdk_device", {
                "snapshot_ref": query["context"]["snapshot_ref"],
                "device_ref": query["items"][0]["device_ref"]})
            # The retired snapshot no longer carries raw detail; the read follows the session.
            assert answer["cache_status"] == "live_session_capture"
            assert answer["device"]["target"]["cell"] == cell
            assert answer["device"]["library"]["resolved_path"] == str(path)
            (path / "temporary-lock").touch()
            (path / "temporary-lock").unlink()
        assert not session.preparation.inspect({})["issues"]
        installed = select(str(tmp_path), library, {})
        bundled = pdk_builtin.BuiltinPdk().package(library)
        assert installed.base.manifest_digest == bundled.manifest_digest
        assert installed.manifest["publication"]["scope"]["devices"] == 6
    # A settled workspace serves the published baseline without any install work.
    settled = session.call("prepare_pdk_data", {"library": library})
    assert settled["source"] == "published_standard" and settled["design_ready"] is True
    assert session.preparation.selected == library
    assert session.call("prepare_pdk_data", {"library": "pdk_a"})["selection_required"]
    assert (tmp_path / ".sico/ai/pdk-data/index.json").exists()
    assert not list((tmp_path / ".sico/ai/pdk-data").glob("*.pdk.json"))


def test_builtin_catalog_projects_the_published_documents():
    builtin = pdk_builtin.BuiltinPdk()
    for library, sample in (("analogLib", "res"), ("basic", "ipin")):
        catalog = builtin.catalog(library)
        package = builtin.package(library)
        devices = package.document("device.json")["items"]
        payload = catalog["payload"]
        assert catalog["source"] == "builtin" and catalog["complete"] is True
        assert len(payload["directory"]["items"]) == len(devices)
        assert payload["coverage"]["device_count"] == len(devices)
        row = payload["devices"][sample]
        assert row["categories"] == devices[sample]["categories"]
        cdf = package.document(devices[sample]["dir"] + "/cdf.json")
        assert row["tiers"]["interface"] == sorted(cdf.get("interface_parameters") or [])
        ready = [name for name, item in payload["devices"].items() if item["readiness"] == "ready"]
        assert len(ready) == 6 and sample in ready
        unpublished = [item for item in payload["devices"].values()
                       if item["readiness"] == "unpublished"]
        assert len(unpublished) == len(devices) - 6
        assert all(item["incomplete_sections"] == ["usage"] for item in unpublished)
        assert payload["coverage"]["metadata_complete"] is False


def test_missing_or_corrupt_builtin_is_an_installation_error(tmp_path, monkeypatch):
    monkeypatch.setattr(pdk_builtin, "ROOT", tmp_path / "builtin")
    bridge = Bridge()
    context_with_pdk(bridge, ("basic",))
    session = PdkSession(bridge=bridge, workspace=tmp_path, environment={})
    with pytest.raises(PdkUnavailable, match="Built-in PDK data is missing"):
        session.call("prepare_pdk_data", {"library": "basic"})
    (tmp_path / "builtin").mkdir()
    (tmp_path / "builtin" / "index.json").write_text("{not json")
    with pytest.raises(PdkUnavailable, match="Invalid built-in PDK data"):
        session.call("search_pdk_devices", {"library": "basic"})
