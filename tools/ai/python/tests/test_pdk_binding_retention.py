"""Bound selections survive discovery churn but still require live validation."""

from copy import deepcopy

import pytest
from pdk_binding_fixture import fixture, prepare_args, tagged

from cadai.circuit_spec_schema import CircuitSpecError
from cadai.pdk_schema import MAX_SNAPSHOTS, PdkUnavailable


def churn(adapter):
    for _ in range(MAX_SNAPSHOTS):
        adapter.session.call("search_pdk_devices", {})


def reselection(adapter, args):
    found = adapter.session.call("search_pdk_devices", {"library": "synthetic"})
    return {**args, "snapshot_ref": found["context"]["snapshot_ref"]}


def test_bound_selection_survives_eviction_and_observes_live_dependencies():
    bridge, adapter, args = fixture()
    record = adapter.bind(args)
    churn(adapter)
    with pytest.raises(PdkUnavailable):
        adapter.session.call("get_pdk_device", {
            k: args[k] for k in ("snapshot_ref", "device_ref")
        })
    before = len(bridge.calls)
    for check in (adapter.validate_prepare(prepare_args(record)),
                  adapter.validate_execute([record["binding_ref"]])):
        assert check[0]["status"] == "valid"
        assert check[0]["cache_used_for_observation"] is False
        assert check[0]["cache_policy"] == "retained_for_bound_adapter"
    assert len(bridge.calls) == before + 2


@pytest.mark.parametrize("change", ["generation", "library", "cdf", "geometry", "body"])
def test_retention_never_bypasses_live_changes(change):
    bridge, adapter, args = fixture()
    record = adapter.bind(args)
    churn(adapter)
    if change == "generation":
        bridge.ctx["bridge_generation"] += 1
    elif change == "library":
        bridge.ctx["libraries"][0]["resolved_path"] += "-changed"
    elif change == "cdf":
        bridge.data["cdf"]["parameters"]["items"][0]["defValue"] = tagged("changed")
    elif change == "geometry":
        bridge.data["database"]["geometry"]["bbox"] = tagged([[-2, -2], [2, 2]])
    else:
        reader = adapter.body_reader
        adapter.body_reader = lambda d: {**reader(d), "signature": "changed"}
    for validate in (lambda: adapter.validate_prepare(prepare_args(record)),
                     lambda: adapter.validate_execute([record["binding_ref"]])):
        with pytest.raises(CircuitSpecError, match="changed"):
            validate()


def test_equivalent_rebinding_preserves_reference_and_original_provenance():
    _, adapter, args = fixture()
    first = adapter.bind(args)
    churn(adapter)
    fresh = reselection(adapter, args)
    assert fresh["snapshot_ref"] != args["snapshot_ref"]
    second = adapter.bind(fresh)
    assert second["binding_ref"] == first["binding_ref"]
    assert second["master"] == first["master"]
    assert second["project_choices"] == first["project_choices"]
    assert len(adapter.records) == 1
    before = prepare_args(first)
    assert adapter.validate_prepare(before)[0]["status"] == "valid"


def test_more_than_eight_bound_searches_remain_usable():
    _, adapter, args = fixture()
    records = []
    for i in range(MAX_SNAPSHOTS + 4):
        selected = reselection(adapter, args)
        records.append(adapter.bind({**selected, "master_id": "device" + str(i)}))
    churn(adapter)
    checks = adapter.validate_execute([r["binding_ref"] for r in records])
    assert all(row["status"] == "valid" for row in checks)
    assert len(adapter.session.store.snapshots) <= MAX_SNAPSHOTS


def test_byte_eviction_also_preserves_bound_validation(monkeypatch):
    from cadai import pdk_store
    from cadai.pdk_normalize import canonical

    _, adapter, args = fixture()
    record = adapter.bind(args)
    size = len(canonical(adapter.session.store.get(args["snapshot_ref"])).encode())
    monkeypatch.setattr(pdk_store, "MAX_CACHE_BYTES", size)
    adapter.session.call("search_pdk_devices", {})
    assert args["snapshot_ref"] not in adapter.session.store.snapshots
    assert adapter.validate_execute([record["binding_ref"]])[0]["status"] == "valid"


def test_retention_capacity_failure_keeps_existing_bindings(monkeypatch):
    from cadai import pdk_store

    _, adapter, args = fixture()
    record = adapter.bind(args)
    fresh = reselection(adapter, args)
    monkeypatch.setattr(pdk_store, "MAX_RETAINED_BYTES", 1)
    with pytest.raises(PdkUnavailable, match="retained"):
        adapter.bind({**fresh, "master_id": "another"})
    assert list(adapter.records) == [record["binding_ref"]]
    churn(adapter)
    assert adapter.validate_execute([record["binding_ref"]])[0]["status"] == "valid"


def test_rejected_binding_does_not_retain_discovery_data():
    bridge, adapter, args = fixture()
    bridge.ctx["bridge_generation"] += 1
    with pytest.raises(CircuitSpecError, match="changed"):
        adapter.bind(args)
    churn(adapter)
    with pytest.raises(PdkUnavailable):
        adapter.session.call("revalidate_pdk_bindings", {
            "snapshot_ref": args["snapshot_ref"], "device_refs": [args["device_ref"]]
        })


def test_binding_reference_changes_with_policy_or_session_generation():
    bridge, adapter, args = fixture()
    first = adapter.bind(args)
    changed = adapter.bind({**args, "project_policy_ref": "test:other-policy"})
    assert changed["binding_ref"] != first["binding_ref"]
    bridge.ctx["bridge_generation"] += 1
    changed_session = adapter.bind(reselection(adapter, args))
    assert changed_session["binding_ref"] != first["binding_ref"]


def test_unbound_details_are_not_retained_with_selected_device():
    _, adapter, args = fixture()
    snap = adapter.session.store.get(args["snapshot_ref"])
    snap["details"]["device:unselected"] = deepcopy(snap["details"][args["device_ref"]])
    adapter.bind(args)
    churn(adapter)
    check = adapter.session.call("revalidate_pdk_bindings", {
        "snapshot_ref": args["snapshot_ref"], "device_refs": ["device:unselected"]
    })
    assert check["status"] == "unavailable"
    assert check["items"][0]["changes"] == ["detail_required"]
