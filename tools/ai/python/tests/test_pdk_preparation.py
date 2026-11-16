import json
from pathlib import Path

import pytest
from pdk_fixture import Bridge
from pdk_publication_fixture import acceptance_evidence, ready_candidate
from sicostate import project_directory

from cadai.pdk_preparation import PdkPreparation
from cadai.pdk_schema import PdkUnavailable
from cadai.pdk_session import PdkSession
from cadai.pdk_standard.publication import publish


def context_with_pdk(bridge, names=("pdk_a",)):
    bridge.ctx["libraries"] = [
        {
            "name": name,
            "resolved_path": "/" + name,
            "status": "complete",
            "technology_library": name,
            "technology_status": "known",
            "pdk_ref": None,
            "pdk_version": None,
            "pdk_status": "unknown",
            "source": "test",
        }
        for name in names
    ]
    bridge.data["identity"]["target"]["library"] = names[0]


def test_multiple_pdks_require_choice_without_device_scan(tmp_path):
    bridge = Bridge()
    context_with_pdk(bridge, ("pdk_a", "pdk_b"))
    preparation = PdkPreparation(bridge, workspace=tmp_path)
    info = preparation.inspect({})
    assert info["selection_required"] is True
    assert [row["library"] for row in info["candidates"]] == ["pdk_a", "pdk_b"]
    before = len(bridge.calls)
    result = preparation.prepare({"choice_confirmed": False})
    assert result["status"] == "selection_required"
    assert result["device_scan_started"] is False
    assert len(bridge.calls) == before + 1  # library context only


def test_collection_does_not_require_a_workflow_mode(tmp_path):
    bridge = Bridge()
    context_with_pdk(bridge, ("pdk_a",))
    preparation = PdkPreparation(
        bridge, workspace=tmp_path, environment={"SICO_PDK_WORKFLOW": "design"})
    result = preparation.prepare({"library": "pdk_a", "choice_confirmed": True})
    assert result["status"] == "collecting"


def test_existing_bound_design_library_skips_pdk_question(tmp_path):
    bridge = Bridge()
    context_with_pdk(bridge, ("smic28hkmg", "other_pdk"))
    bridge.data["identity"]["target"]["library"] = "sxTest"
    bridge.ctx["libraries"].insert(0, {
        "name": "sxTest", "resolved_path": str(tmp_path / "sxTest"),
        "status": "complete", "technology_library": "smic28hkmg",
        "technology_status": "known", "pdk_ref": None, "pdk_version": None,
        "pdk_status": "unknown", "source": "test",
    })
    preparation = PdkPreparation(bridge, workspace=tmp_path)
    result = preparation.inspect({"target_library": "sxTest"})
    assert result["selection_required"] is False
    assert result["choice_confirmed"] is True
    assert result["selected_library"] == "smic28hkmg"
    assert result["selection_source"] == "technology_binding"


def test_entry_context_failure_does_not_block_explicit_pdk_selection(tmp_path):
    bridge = Bridge()
    context_with_pdk(bridge, ("pdk_a",))

    def unavailable_entry():
        raise RuntimeError("entry unavailable")

    preparation = PdkPreparation(
        bridge,
        workspace=tmp_path,
        environment={},
        entry=unavailable_entry,
    )
    result = preparation.inspect({"target_library": "pdk_a"})
    assert result["status"] == "preparation_required"
    assert result["selected_library"] == "pdk_a"


def test_choice_must_name_a_reported_pdk_candidate():
    bridge = Bridge()
    context_with_pdk(bridge, ("pdk_a", "pdk_b"))
    preparation = PdkPreparation(bridge)
    result = preparation.prepare({"library": "unreported", "choice_confirmed": True})
    assert result["status"] == "selection_required"
    assert result["next_action"] == "ask_user_to_select_pdk"
    assert result["device_scan_started"] is False


def test_collection_is_batched_and_repeated_batch_does_not_rescan(tmp_path):
    bridge = Bridge()
    context_with_pdk(bridge, ("pdk_a",))
    preparation = PdkPreparation(bridge, workspace=tmp_path)
    job = preparation.prepare({"library": "pdk_a", "choice_confirmed": True})
    ref = job["collection_ref"]
    done = preparation.advance({"collection_ref": ref, "next_batch": 0})
    assert done["status"] == "ready"
    calls = len(bridge.calls)
    repeated = preparation.advance({"collection_ref": ref, "next_batch": 0})
    assert repeated["status"] == "ready"
    assert len(bridge.calls) == calls
    search = PdkSession(bridge=bridge, workspace=tmp_path)
    # The prepared directory is reused; search itself does not capture a directory.
    search.preparation.ready[("pdk_a", "symbol")] = preparation.ready[("pdk_a", "symbol")]
    result = search.call("search_pdk_devices", {"library": "pdk_a"})
    assert result["source_status"] == "persisted_directory_live_context"
    # A new session performs the cheap library binding check, but must not
    # recapture the device directory for a query.
    assert not any(op == "directory" for op, _ in bridge.calls[calls:])


def test_changed_session_restarts_read_only_collection_automatically(tmp_path):
    bridge = Bridge()
    context_with_pdk(bridge, ("pdk_a",))
    preparation = PdkPreparation(bridge, workspace=tmp_path)
    job = preparation.prepare({"library": "pdk_a", "choice_confirmed": True})
    bridge.ctx["session_ref"] = "session:changed"
    failed = preparation.advance({"collection_ref": job["collection_ref"], "next_batch": 0})
    assert failed["status"] == "failed" and failed["code"] == "collection_changed"
    assert preparation.jobs[job["collection_ref"]]["status"] == "failed"
    retry = preparation.prepare({"library": "pdk_a", "choice_confirmed": True})
    assert retry["status"] == "collecting"
    assert retry["collection_ref"] != job["collection_ref"]


def finish(preparation, job):
    while job["status"] == "collecting":
        job = preparation.advance({k: job[k] for k in ("collection_ref", "next_batch")})
    return job


def test_missing_configured_data_builds_full_database_on_search(tmp_path):
    bridge = Bridge()
    context_with_pdk(bridge)
    bridge.count = 7
    session = PdkSession(bridge=bridge, workspace=tmp_path,
                         environment={"SICO_PDK_DATA": "missing.json"})
    job = session.call("search_pdk_devices", {})
    assert job["status"] == "collecting"
    progress = session.preparation.advance({k: job[k] for k in ("collection_ref", "next_batch")})
    assert progress["status"] == "collecting" and progress["detailed_devices"] == 4
    assert not list((tmp_path / ".sico/ai/pdk-data").glob("*.pdk.json"))
    done = finish(session.preparation, progress)
    published = json.loads(Path(done["path"]).read_text())
    assert published["schema"] == "cad.pdk.database.v2"
    assert done["coverage"]["details_complete"] and done["device_count"] == 7
    root = tmp_path / ".sico/ai/pdk-cache/devices"
    devices = [json.loads(path.read_text())["device"] for path in root.glob("*.json")]
    assert len(devices) == 7
    assert all(row["parameters"]["items"][0]["name"] == "w" for row in devices)
    assert all(row["callbacks"]["items"][0]["execution_policy"] == "never_execute_during_discovery" for row in devices)
    assert "collection_ref" not in session.call("search_pdk_devices", {})


def test_interrupted_details_resume_without_repeating_completed_devices(tmp_path):
    bridge = Bridge()
    context_with_pdk(bridge)
    bridge.count = 7
    first = PdkPreparation(bridge, workspace=tmp_path, environment={})
    job = first.prepare({})
    progress = first.advance({k: job[k] for k in ("collection_ref", "next_batch")})
    resumed = PdkPreparation(bridge, workspace=tmp_path, environment={})
    job = resumed.prepare({})
    assert job["collection_ref"] == progress["collection_ref"] and job["detailed_devices"] == 4
    before = len(bridge.calls)
    assert finish(resumed, job)["status"] == "ready"
    assert [target["cell"] for op, target in bridge.calls[before:] if op == "device"] == ["cell4", "cell5", "cell6"]


def test_missing_device_file_and_legacy_directory_are_recollected(tmp_path):
    bridge = Bridge()
    context_with_pdk(bridge)
    first = PdkPreparation(bridge, workspace=tmp_path, environment={})
    legacy = first.data.publish(bridge.capture("directory", {}), "pdk_a", "symbol")
    job = first.prepare({})
    assert job["status"] == "collecting"
    done = finish(first, job)
    value = json.loads(Path(done["path"]).read_text())
    key = next(iter(value["payload"]["devices"].values()))["digest"]
    (tmp_path / ".sico/ai/pdk-cache/devices" / (key + ".json")).unlink()
    second = PdkPreparation(bridge, workspace=tmp_path, environment={"SICO_PDK_DATA": legacy["path"]})
    assert second.prepare({})["status"] == "collecting"


def test_cancel_is_respected_and_retry_budget_stops_persistent_failure(tmp_path):
    bridge = Bridge()
    context_with_pdk(bridge)
    service = PdkPreparation(bridge, workspace=tmp_path, environment={})
    job = service.prepare({})
    service.cancel({"collection_ref": job["collection_ref"]})
    assert service.prepare({})["status"] == "cancelled"
    job = service.prepare({"refresh": True})
    bridge.data["cdf"]["parameters"]["items"] = []
    for _ in range(3):
        assert service.advance({k: job[k] for k in ("collection_ref", "next_batch")})["status"] == "failed"
        job = service.prepare({})
    assert job["status"] == "failed" and job["next_action"] == "inspect_collection_failure"


def test_ready_cache_recovers_deleted_detail_in_same_process(tmp_path):
    bridge = Bridge()
    context_with_pdk(bridge)
    service = PdkPreparation(bridge, workspace=tmp_path, environment={})
    finish(service, service.prepare({}))
    next(service.data.root.glob("devices/*.json")).unlink()
    job = service.prepare({})
    assert job["status"] == "collecting" and job["detailed_devices"] == 0
    assert finish(service, job)["status"] == "ready"


def test_damaged_checkpoint_restarts_and_missing_metadata_is_reported(tmp_path):
    bridge = Bridge()
    context_with_pdk(bridge)
    bridge.count = 7
    service = PdkPreparation(bridge, workspace=tmp_path, environment={})
    job = service.advance({**{k: v for k, v in service.prepare({}).items()
                             if k in ("collection_ref", "next_batch")}})
    next(service.data.root.glob("devices/*.json")).unlink()
    resumed = PdkPreparation(bridge, workspace=tmp_path, environment={})
    restart = resumed.prepare({})
    assert restart["collection_ref"] != job["collection_ref"]
    assert restart["detailed_devices"] == 0
    bridge.data["database"]["ports"]["status"] = "unavailable"
    done = finish(resumed, restart)
    assert done["status"] == "ready" and done["coverage"]["details_complete"]
    assert not done["coverage"]["metadata_complete"]
    assert done["coverage"]["incomplete_device_count"] == 7
    assert "ports" in done["coverage"]["incomplete_devices"]["cell0"]


@pytest.mark.parametrize("bad_context", [None, {}, "damaged"])
def test_corrupt_checkpoint_context_restarts_collection(tmp_path, bad_context):
    bridge = Bridge()
    context_with_pdk(bridge)
    service = PdkPreparation(bridge, workspace=tmp_path, environment={})
    original = service.prepare({})
    checkpoint = next(service.data.root.glob("collections/*.json"))
    value = json.loads(checkpoint.read_text())
    value["capture"]["context"] = bad_context
    checkpoint.write_text(json.dumps(value))
    resumed = PdkPreparation(bridge, workspace=tmp_path, environment={})
    job = resumed.prepare({})
    assert job["collection_ref"] != original["collection_ref"]
    assert finish(resumed, job)["status"] == "ready"


@pytest.mark.parametrize("field,value", [
    ("elapsed_seconds", None),
    ("elapsed_seconds", -1),
    ("device_count", "damaged"),
    ("total_cells", "damaged"),
    ("devices", {"cell0": None}),
    ("devices", {"cell0": {"incomplete_sections": []}}),
])
def test_corrupt_checkpoint_progress_restarts_collection(tmp_path, field, value):
    bridge = Bridge()
    context_with_pdk(bridge)
    service = PdkPreparation(bridge, workspace=tmp_path, environment={})
    original = service.prepare({})
    checkpoint = next(service.data.root.glob("collections/*.json"))
    record = json.loads(checkpoint.read_text())
    record[field] = value
    checkpoint.write_text(json.dumps(record))
    resumed = PdkPreparation(bridge, workspace=tmp_path, environment={})
    job = resumed.prepare({})
    assert job["collection_ref"] != original["collection_ref"]
    assert finish(resumed, job)["status"] == "ready"


def test_transport_exception_resumes_without_recapturing_completed_details(tmp_path, monkeypatch):
    bridge = Bridge()
    context_with_pdk(bridge)
    bridge.count = 7
    capture = bridge.capture
    failed_once = False

    def flaky(op, target):
        nonlocal failed_once
        if op == "device" and target["cell"] == "cell2" and not failed_once:
            failed_once = True
            raise OSError("transport disconnected")
        return capture(op, target)

    monkeypatch.setattr(bridge, "capture", flaky)
    service = PdkPreparation(bridge, workspace=tmp_path, environment={})
    job = service.prepare({})
    with pytest.raises(OSError, match="transport"):
        service.advance({k: job[k] for k in ("collection_ref", "next_batch")})
    checkpoint = json.loads(next(service.data.root.glob("collections/*.json")).read_text())
    assert checkpoint["status"] == "failed" and checkpoint["detailed_devices"] == 2
    before = len(bridge.calls)
    assert finish(service, service.prepare({}))["status"] == "ready"
    assert [target["cell"] for op, target in bridge.calls[before:] if op == "device"] == [
        "cell2", "cell3", "cell4", "cell5", "cell6"]


def test_rejected_directory_batch_does_not_mutate_checkpoint(tmp_path, monkeypatch):
    bridge = Bridge()
    context_with_pdk(bridge)
    bridge.count = 2
    capture = bridge.capture
    duplicate = True

    def batch(op, target):
        result = capture(op, target)
        if op == "directory":
            offset = target["offset"]
            result["data"].update(offset=offset, scanned_cells=1, total_cells=2,
                                  next_offset=1 if offset == 0 else None)
            result["data"]["items"] = [result["data"]["items"][0 if duplicate else offset]]
        return result

    monkeypatch.setattr(bridge, "capture", batch)
    service = PdkPreparation(bridge, workspace=tmp_path, environment={})
    job = service.prepare({})
    job = service.advance({k: job[k] for k in ("collection_ref", "next_batch")})
    failed = service.advance({k: job[k] for k in ("collection_ref", "next_batch")})
    assert failed["status"] == "failed"
    retained = service.jobs[job["collection_ref"]]
    assert retained["scanned_cells"] == 1 and len(retained["capture"]["data"]["items"]) == 1
    duplicate = False
    assert finish(service, service.prepare({}))["status"] == "ready"


def test_inspect_requires_baseline_choice_before_collection(tmp_path):
    shared, local = tmp_path / "shared", tmp_path / "local"
    current, _, _ = ready_candidate(shared)
    publish(shared, "synthetic", current.revision, {},
            acceptance=acceptance_evidence(shared))
    current, _, _ = ready_candidate(local)
    publish(local, "synthetic", current.revision, {},
            acceptance=acceptance_evidence(local))
    bridge = Bridge()
    context_with_pdk(bridge, ("synthetic",))
    service = PdkPreparation(
        bridge,
        workspace=local,
        environment={"SICO_PDK_DATA": str(project_directory(shared, "ai/pdk-data"))},
    )
    info = service.inspect({})
    assert info["status"] == "decision_required"
    assert info["next_action"] == "ask_user_to_resolve_pdk_baseline"
    assert info["baseline_sync_required"] is True
    assert info["baseline_sync_question"]["id"] == "pdk_baseline_sync"
    assert info["candidates"][0]["standard"]["status"] == "decision_required"
    assert any(issue.get("code") == "pdk_baseline_sync_required" for issue in info["issues"])
    assert not service.jobs and not service.ready


def test_inspect_points_published_standard_at_get_pdk_data(tmp_path):
    current, _, _ = ready_candidate(tmp_path)
    publish(tmp_path, "synthetic", current.revision, {},
            acceptance=acceptance_evidence(tmp_path))
    bridge = Bridge()
    context_with_pdk(bridge, ("synthetic",))
    service = PdkPreparation(bridge, workspace=tmp_path, environment={})
    info = service.inspect({})
    assert info["candidates"][0]["standard"]["status"] == "selected"
    assert info["next_action"] == "get_pdk_data"
    assert not service.jobs and not service.ready


def test_published_standard_ready_survives_a_followup_inspection(tmp_path):
    current, bridge, _ = ready_candidate(tmp_path)
    publish(tmp_path, "synthetic", current.revision, {},
            acceptance=acceptance_evidence(tmp_path))
    service = PdkPreparation(bridge, workspace=tmp_path, environment={})
    ready = service.prepare({"library": "synthetic", "view": "symbol",
                             "choice_confirmed": True})
    assert ready["status"] == "ready" and ready["design_ready"] is True
    info = service.inspect({"view": "symbol"})
    assert info["issues"] == []
    assert ("synthetic", "symbol") in service.ready
    capture, selection = service.directory({"library": "synthetic", "view": "symbol"})
    assert capture is not None and selection == "published_standard"


def test_design_refresh_reuses_ready_standard_without_collection(tmp_path):
    current, bridge, _ = ready_candidate(tmp_path)
    publish(tmp_path, "synthetic", current.revision, {},
            acceptance=acceptance_evidence(tmp_path))
    service = PdkPreparation(bridge, workspace=tmp_path, environment={})
    ready = service.prepare({"library": "synthetic", "view": "symbol",
                             "choice_confirmed": True, "refresh": True})
    assert ready["status"] == "ready" and ready["design_ready"] is True
    assert not service.jobs
