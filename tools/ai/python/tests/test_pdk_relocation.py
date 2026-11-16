"""Publishing a collected PDK to a shared install must preserve cached reads."""
import json
from pathlib import Path
import shutil

import pytest

from cadai.pdk_normalize import digest
from cadai.pdk_relocation import replace_paths
from cadai.pdk_session import PdkSession
from pdk_binding_fixture import tagged
from pdk_fixture import Bridge
from test_pdk_preparation import context_with_pdk, finish


def relocated(tmp_path):
    bridge = Bridge()
    context_with_pdk(bridge)
    old = tmp_path / "capture-install" / "pdk_a"
    old.mkdir(parents=True)
    bridge.ctx["libraries"][0]["resolved_path"] = str(old)
    bridge.data["identity"]["resolved_view_path"] = str(old / "cell0/symbol")
    bridge.data["cdf"]["parameters"]["items"][0]["callback"] = tagged(
        'load("' + str(old.parent / 'rules.il') + '")')
    bridge.data["database"]["geometry"]["items"] = [{
        "terminal": "DATA<3:0>", "pin_index": 0, "figure_index": 0, "status": "complete",
        "raw": {"bBox": tagged([[-1, -1], [1, 1]]), "objType": tagged("rect")},
    }]
    native = bridge.capture

    def capture(op, target):
        result = native(op, target)
        records = [result["data"]] if op == "device" else result["data"].get("items", [])
        if op in {"device", "directory"}:
            for row in records:
                identity = row["identity"]
                identity["resolved_view_path"] = str(Path(bridge.ctx["libraries"][0]["resolved_path"]) /
                    identity["target"]["cell"] / identity["target"]["view"])
        return result

    bridge.capture = capture
    (tmp_path / "collector").mkdir()
    (tmp_path / "consumer").mkdir()
    first = PdkSession(bridge=bridge, workspace=tmp_path / "collector", environment={})
    done = finish(first.preparation, first.call("prepare_pdk_data", {}))
    source = Path(done["path"]).parent
    shared = tmp_path / "shared-data"
    shutil.copytree(source, shared)
    new = tmp_path / "shared-install" / "pdk_a"
    new.mkdir(parents=True)
    bridge.ctx["libraries"][0]["resolved_path"] = str(new)
    bridge.data = replace_paths(bridge.data, {str(old): str(new), str(old.parent): str(new.parent)})
    shutil.rmtree(old.parent)
    session = PdkSession(bridge=bridge, workspace=tmp_path / "consumer",
                         environment={"SICO_PDK_DATA": str(shared)})
    bridge.calls.clear()
    return bridge, session, shared, old, new


@pytest.mark.parametrize("evict", [False, True])
def test_moved_pdk_is_announced_and_reused_without_old_install_or_live_scan(tmp_path, evict):
    bridge, session, shared, old, new = relocated(tmp_path)
    before = {p: p.read_bytes() for p in shared.rglob('*.json')}
    info = session.call("get_pdk_preparation", {})
    assert not info["issues"]
    notice = info["notices"][0]
    assert notice["code"] == "pdk_path_changed" and notice["user_input_required"] is False
    assert str(old) in notice["message"] and str(new) in notice["message"]
    ready = session.call("prepare_pdk_data", {})
    assert ready["status"] == "ready" and ready["source"] == "environment"
    assert ready["notices"] == info["notices"]
    query = session.call("search_pdk_devices", {"library": "pdk_a", "cell": "cell0"})
    assert query["notices"] == info["notices"]
    args = {"snapshot_ref": query["context"]["snapshot_ref"], "device_ref": query["items"][0]["device_ref"]}
    answer = session.call("get_pdk_device", args)
    assert answer["cache_status"] == "persisted_database"
    value = answer["device"]
    assert value["library"]["resolved_path"] == str(new)
    assert value["device_ref"] == args["device_ref"]
    assert all(callback["owner"]["device_ref"] == args["device_ref"] for callback in value["callbacks"]["items"])
    callback = value["callbacks"]["items"][0]
    assert str(new.parent) in callback["raw"]["value"]
    assert callback["callback_ref"] == "callback:" + digest([callback["owner"], callback["raw"]])
    figure = value["geometry"]["items"][0]
    assert figure["port_ref"] == value["ports"]["items"][0]["port_ref"]
    assert figure["anchors"]["candidates"][0]["source_ref"] == figure["figure_ref"]
    assert not session.preparation.jobs
    assert all(op == "libraries" for op, _ in bridge.calls)
    assert all(p.read_bytes() == raw for p, raw in before.items())
    if evict:
        session.retain_binding(args["snapshot_ref"], args["device_ref"])
        for _ in range(8):
            session.call("search_pdk_devices", {"library": "pdk_a", "cell": "cell0"})
    check = session.call("revalidate_pdk_bindings", {"snapshot_ref": args["snapshot_ref"], "device_refs": [args["device_ref"]]})
    assert check["status"] == "valid", check
    bridge.data["cdf"]["parameters"]["items"][0]["defValue"]["value"] = "changed"
    assert session.call("revalidate_pdk_bindings", {"snapshot_ref": args["snapshot_ref"], "device_refs": [args["device_ref"]]})["status"] == "changed"


def test_changed_enrichment_rules_require_fresh_collection(tmp_path):
    _, session, shared, _, _ = relocated(tmp_path)
    manifest = next(shared.glob("*.pdk.json"))
    value = json.loads(manifest.read_text())
    value["payload"]["binding"].pop("enrichment_revision")
    value["digest"] = digest(value["payload"])
    manifest.write_text(json.dumps(value))
    info = session.call("get_pdk_preparation", {})
    assert any(issue["code"] == "stale_pdk_data" for issue in info["issues"]), info
    assert not session.preparation.catalogs


@pytest.mark.parametrize("damage", ["missing_device", "bad_digest", "collector", "technology"])
def test_relocation_does_not_hide_missing_data_or_real_binding_changes(tmp_path, damage):
    bridge, session, shared, _, _ = relocated(tmp_path)
    manifest = next(shared.glob('*.pdk.json'))
    value = json.loads(manifest.read_text())
    if damage == "missing_device":
        next((shared / "devices").glob('*.json')).unlink()
    elif damage == "bad_digest":
        value["digest"] = "0" * 64
        manifest.write_text(json.dumps(value))
    elif damage == "collector":
        bridge.ctx["collector_revision"] = "changed"
    else:
        bridge.ctx["libraries"][0]["technology_library"] = "changed"
    info = session.call("get_pdk_preparation", {})
    assert info["issues"] and not info["notices"]


def test_paths_are_replaced_once_at_boundaries_in_multi_root_expressions():
    value = {'expression': 'load("/old/lib/file") load("/tech/rules")',
             'paths': ['/old/lib', '/old/lib2/a', '/else/old/lib/a', '/old/models/deck']}
    assert replace_paths(value, {'/old/lib': '/old/shared/lib', '/old': '/shared', '/tech': '/newtech'}) == {
        'expression': 'load("/old/shared/lib/file") load("/newtech/rules")',
        'paths': ['/old/shared/lib', '/shared/lib2/a', '/else/old/lib/a', '/shared/models/deck']}
