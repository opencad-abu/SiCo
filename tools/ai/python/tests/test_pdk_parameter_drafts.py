"""Manual mapping recovery, exact batch scope, stale sources and consent reuse."""

import pytest
from pdk_draft_fixture import approve, create, entry, restart, setup, source, submit
from pdk_standard_fixture import replace

from cadai.pdk_errors import PdkArgumentError, PdkUnavailable
from cadai.pdk_standard.jsonio import LIMIT
from cadai.pdk_standard.service import StandardData
from cadai.pdk_standard.tools import arguments
from cadai.pdk_standard.workspace import select


def test_source_mapping_restart_and_host_only_application(tmp_path):
    service, bridge, _ = setup(tmp_path)
    before = select(tmp_path, "synthetic", {}).revision
    draft = create(service)
    assert draft["source"]["state"] == "verified"
    assert draft["items"][0]["form_label"] == "Test width"
    pending = submit(service, draft)
    resumed = restart(service, bridge)
    assert resumed.updates.restore(pending["update_ref"]) == pending
    assert resumed.updates.apply({"update_ref": pending["update_ref"]}) == pending
    assert select(tmp_path, "synthetic", {}).revision == before
    applied = approve(resumed, pending)
    assert applied["status"] == "ok"
    again = restart(resumed, bridge)
    assert again.updates.apply({"update_ref": pending["update_ref"]}) == applied
    state = again.drafts.get({"draft_ref": draft["draft_ref"], "section": "batches"})
    assert state["items"][0]["status"] == "applied"
    assert state["draft_status"] == "open"
    cdf = select(tmp_path, "synthetic", {}).document("cell0/cdf.json")
    assert "manual_bytes" in cdf["depends_on"]
    assert cdf["parameters"]["w"]["write"] == "allow"


def test_next_batch_and_source_valid_confirmation_reuse(tmp_path):
    service, bridge, _ = setup(tmp_path)
    draft = create(service, [entry(), entry("control", "p1", "second", manual_term="Control")])
    approved = submit(service, draft)
    approve(service, approved)
    fresh = service.drafts.get({"draft_ref": draft["draft_ref"]})
    next_review = submit(
        service,
        fresh,
        ["control"],
        "second",
        [
            {
                "file": "cell0/cdf.json",
                "ops": [
                    replace("/parameters/p1/write", "deny"),
                    replace("/parameters/p1/requirement", "not_applicable"),
                ],
            }
        ],
    )
    approve(service, next_review)
    reuse = create(service)
    result = submit(service, reuse)
    assert result["status"] == "reused" and not result["confirmation_required"]
    assert len(service.updates.previews) == 2
    before = select(tmp_path, "synthetic", {}).revision
    assert submit(service, service.drafts.get({"draft_ref": reuse["draft_ref"]})) == result
    assert select(tmp_path, "synthetic", {}).revision == before


@pytest.mark.parametrize("change", ["unknown", "meaning", "scope", "execution"])
def test_reject_unknown_and_unmapped_or_fact_patches(tmp_path, change):
    service, _, _ = setup(tmp_path)
    if change == "unknown":
        with pytest.raises(PdkUnavailable, match="not present"):
            create(service, [entry(cdf="invented")])
        return
    draft = create(service)
    operations = {
        "meaning": replace("/parameters/w/meaning", "fabricated fact"),
        "scope": replace("/parameters/p1/write", "allow"),
        "execution": replace("/execution", {"mode": "literal"}),
    }
    with pytest.raises(PdkUnavailable, match="mapped policy"):
        submit(service, draft, changes=[{"file": "cell0/cdf.json", "ops": [operations[change]]}])
    assert not service.updates.previews


def test_unavailable_manual_and_ambiguity_are_durable_not_authority(tmp_path):
    service, _, _ = setup(tmp_path)
    row = entry(status="unresolved", difference="No matching manual for this installation")
    draft = create(service, [row], {"unavailable_reason": "Vendor manual absent"})
    assert draft["source"]["state"] == "unavailable"
    with pytest.raises(PdkUnavailable, match="Unresolved"):
        submit(service, draft)
    with pytest.raises(PdkUnavailable, match="Missing manual"):
        create(service, manual_source={"unavailable_reason": "absent"})


@pytest.mark.parametrize("change", ["manual", "cdf", "cancel"])
def test_source_or_cancellation_after_review_blocks_apply(tmp_path, change):
    service, bridge, manual = setup(tmp_path)
    draft = create(service)
    pending = submit(service, draft)
    before = select(tmp_path, "synthetic", {}).revision
    if change == "manual":
        manual.write_text("changed bytes")
    elif change == "cdf":
        bridge.data["cdf"]["parameters"]["items"][0]["prompt"]["value"] = "Other meaning"
    else:
        fresh = service.drafts.get({"draft_ref": draft["draft_ref"]})
        service.drafts.cancel(
            {"draft_ref": draft["draft_ref"], "draft_version": fresh["draft_version"]}
        )
    with pytest.raises(PdkUnavailable):
        approve(service, pending)
    assert select(tmp_path, "synthetic", {}).revision == before


def test_cancel_answer_is_durable_and_modified_retry_is_rejected(tmp_path):
    service, bridge, _ = setup(tmp_path)
    draft = create(service)
    pending = submit(service, draft)
    fresh = service.drafts.get({"draft_ref": draft["draft_ref"]})
    assert submit(service, fresh) == pending
    with pytest.raises(PdkUnavailable, match="different contents"):
        submit(
            service,
            fresh,
            changes=[{"file": "cell0/cdf.json", "ops": [replace("/parameters/w/write", "deny")]}],
        )
    service.updates.confirmation = lambda _: {"decision": "cancel"}
    cancelled = service.updates.apply({"update_ref": pending["update_ref"]})
    assert (
        restart(service, bridge).updates.apply({"update_ref": pending["update_ref"]}) == cancelled
    )


def test_unsubmitted_upsert_cas_pagination_and_exception(tmp_path):
    service, _, _ = setup(tmp_path, 40)
    rows = [
        entry("p" + str(i), "w" if i == 0 else "p" + str(i), "batch" + str(i)) for i in range(32)
    ]
    draft = create(service, rows)
    second_page = service.drafts.get(
        {"draft_ref": draft["draft_ref"], "cursor": draft["next_cursor"]}
    )
    assert len(second_page["items"]) == 12
    changed = {**rows[0], "user_term": "Special device width"}
    args = {
        "library": "synthetic",
        "revision": draft["revision"],
        "source": source(),
        "draft_ref": draft["draft_ref"],
        "draft_version": draft["draft_version"],
        "mode": "upsert",
        "entries": [changed],
    }
    updated = service.drafts.prepare(args)
    with pytest.raises(PdkUnavailable, match="cursor"):
        service.drafts.get({"draft_ref": draft["draft_ref"], "cursor": draft["next_cursor"]})
    with pytest.raises(PdkUnavailable, match="revision"):
        service.drafts.prepare(args)
    assert updated["items"][0]["user_term"] == "Special device width"
    assert all(path.stat().st_size <= LIMIT for path in service.drafts.store.root.rglob("*.json"))


def test_interrupted_result_write_recovers_committed_overlay(tmp_path, monkeypatch):
    service, bridge, _ = setup(tmp_path)
    draft = create(service)
    pending = submit(service, draft)
    monkeypatch.setattr(
        service.updates.reviews, "finish", lambda *_: (_ for _ in ()).throw(OSError("crash"))
    )
    with pytest.raises(OSError, match="crash"):
        approve(service, pending)
    current = select(tmp_path, "synthetic", {}).revision
    recovered = restart(service, bridge).updates.apply({"update_ref": pending["update_ref"]})
    assert recovered["status"] == "ok" and recovered["recovered"]
    assert select(tmp_path, "synthetic", {}).revision == current


def test_contract_registration_design_gate_and_path_traversal(tmp_path):
    from sico.adapters.circuit import NAMES

    service, _, _ = setup(tmp_path)
    assert {
        "prepare_pdk_parameter_draft",
        "get_pdk_parameter_draft",
        "submit_pdk_parameter_batch",
        "cancel_pdk_parameter_draft",
    } <= NAMES
    with pytest.raises(PdkUnavailable, match="Normal design"):
        StandardData(tmp_path, {}).call("get_pdk_parameter_draft", {})
    with pytest.raises(PdkUnavailable, match="Invalid update"):
        service.updates.restore("pdk-update:../../../etc/passwd")
    with pytest.raises(PdkArgumentError):
        arguments(
            "prepare_pdk_parameter_draft",
            {
                "library": "synthetic",
                "revision": "r",
                "source": source(),
                "entries": [entry()],
                "draft_ref": "pdk-draft:" + "1" * 32,
            },
        )


def test_device_usage_and_conditional_parameter_batches(tmp_path):
    service, _, _ = setup(tmp_path)
    row = entry("device", role="device", manual_term="Device catalog")
    del row["cdf"]
    draft = create(service, [row])
    preview = submit(
        service,
        draft,
        ["device"],
        changes=[{"file": "device.json", "ops": [replace("/items/cell0/use/circuit", "allow")]}],
    )
    assert approve(service, preview)["status"] == "ok"
    conditional = create(service, [entry(), entry("selector", "p1", role="selector")])
    # Use the standard condition grammar rather than a second draft rule language.
    from cadai.pdk_standard.constraints import condition

    mode = {"op": "eq", "left": {"param": "p1"}, "right": 0.3}
    condition(mode, select(tmp_path, "synthetic", {}).document("cell0/cdf.json")["parameters"])
    preview = submit(
        service,
        conditional,
        ["width", "selector"],
        changes=[
            {
                "file": "cell0/cdf.json",
                "ops": [
                    replace("/parameters/w/write", "conditional"),
                    {"op": "add", "path": "/parameters/w/write_when", "value": mode},
                    replace("/parameters/p1/write", "allow"),
                ],
            }
        ],
    )
    assert approve(service, preview)["status"] == "ok"


def test_absent_source_read_is_stale_and_get_does_not_write(tmp_path):
    service, _, manual = setup(tmp_path)
    draft = create(service)
    before = {p: p.read_bytes() for p in service.drafts.store.root.rglob("*.json")}
    manual.unlink()
    state = service.drafts.get({"draft_ref": draft["draft_ref"]})
    assert state["draft_status"] == "stale"
    assert state["source_error"]["code"] == "pdk_source_changed"
    assert {p: p.read_bytes() for p in service.drafts.store.root.rglob("*.json")} == before


def test_same_target_operations_cannot_change_meaning_when_noops_are_reused(tmp_path):
    service, _, _ = setup(tmp_path)
    first = create(service)
    approve(service, submit(service, first))
    draft = create(service)
    with pytest.raises(PdkUnavailable, match="nonoverlapping"):
        submit(
            service,
            draft,
            changes=[
                {
                    "file": "cell0/cdf.json",
                    "ops": [
                        replace("/parameters/w/write", "deny"),
                        replace("/parameters/w/write", "allow"),
                    ],
                }
            ],
        )


def test_reused_batch_retry_checks_current_cdf_source(tmp_path):
    service, bridge, _ = setup(tmp_path)
    draft = create(service)
    approve(service, submit(service, draft))
    reuse = create(service)
    assert submit(service, reuse)["status"] == "reused"
    current = service.drafts.get({"draft_ref": reuse["draft_ref"]})
    bridge.data["cdf"]["parameters"]["items"][0]["prompt"]["value"] = "Changed source"
    with pytest.raises(PdkUnavailable):
        submit(service, current)


@pytest.mark.parametrize("field", ["meaning", "user_term"])
def test_incomplete_terminology_must_remain_unresolved(tmp_path, field):
    service, _, _ = setup(tmp_path)
    row = entry()
    del row[field]
    with pytest.raises(PdkUnavailable, match="meaning and user terminology"):
        create(service, [row])
    row.update(status="unresolved", difference="Terminology not established")
    assert create(service, [row])["items"][0]["status"] == "unresolved"
