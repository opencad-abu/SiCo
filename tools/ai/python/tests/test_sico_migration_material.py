"""Read historical result paths through the reviewed, immutable migration inventory."""

import json
from pathlib import Path

import pytest

from cadai.result_contract import ResultError
from cadai.result_tools import ResultStore
from sico.migration.activation import activate
from sico.migration.audit import audit
from sico.migration.prepare import prepare
from sico.storage.history import SessionReader
from sico.storage.journal import SessionJournal
from legacy_state_fixtures import legacy_writer
from legacy_state_fixtures import save_release
from sico.storage.workbench import WorkbenchIndex
from test_result_contract import fixture_data
from test_sico_migration_sessions import seed


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    observe = lambda _source: {"scope": "local", "referencing_pids": [], "unreadable_processes": 0}
    monkeypatch.setattr("sico.migration.audit.scan_local", observe)
    monkeypatch.setattr("sico.migration.prepare.scan_local", observe)
    directory, address, context = seed(tmp_path, status="executing")
    _, _, snapshot, _contract = fixture_data()
    with legacy_writer():
        result_ref, artifact = ResultStore(tmp_path).put("results", snapshot)
        with SessionJournal(tmp_path, "session") as journal:
            state = journal.state
            invocation = {"id": "read-result", "name": "read_maestro_results", "input": {}}
            journal.append("tool.started", invocation, state)
            event = journal.append("tool.finished", {
                "id": invocation["id"], "name": invocation["name"],
                "result": {"status": "ok", "data": {"result_ref": result_ref,
                                                   "artifact": artifact}},
            }, state)
            state.task["status"] = "completed"
            journal.append("task.completed", {}, state)
            ended = journal.append_session("session.end_observed", dict(
                address=address.record(), operation_id="end", state="drained"), context)
        save_release(directory, address, "end", ended["sequence"])
    report = audit(tmp_path)
    assert not report["blockers"], report["blockers"]
    preparation = prepare(tmp_path, expected_inventory=report["inventory_sha256"], writers_stopped=True)
    activate(tmp_path, expected_inventory=report["inventory_sha256"], writers_stopped=True)
    return dict(project=tmp_path, transaction=Path(preparation["transaction"]),
                artifact=artifact, snapshot=snapshot, key=f"d_{event['sequence']}_tool")


def index_for(fixture, root=".sico"):
    return WorkbenchIndex(SessionReader(fixture["project"] / root / "ai/agent", "session")).sync()


def test_material_reads_new_copy_without_rewriting_historical_absolute_path(migrated):
    source = Path(migrated["artifact"]["path"])
    source_bytes = source.read_bytes()
    source.write_text("{}")
    index = index_for(migrated)
    assert index.material(migrated["key"]) == migrated["snapshot"]
    assert index.data[migrated["key"]]["value"]["data"]["artifact"] == migrated["artifact"]
    target = migrated["project"] / ".sico/ai/measurement-results" / source.name
    assert target.read_bytes() == source_bytes


@pytest.mark.parametrize("damage", ["missing", "digest", "symlink", "directory", "inventory"])
def test_material_rejects_damaged_target_or_mapping_without_using_old_copy(migrated, damage):
    index = index_for(migrated)
    target = migrated["project"] / ".sico/ai/measurement-results" / Path(migrated["artifact"]["path"]).name
    if damage == "missing":
        target.unlink()
    elif damage == "digest":
        target.write_text("{}")
    elif damage == "symlink":
        target.unlink()
        target.symlink_to(migrated["artifact"]["path"])
    elif damage == "directory":
        target.parent.rename(target.parent.with_name("moved"))
        target.parent.symlink_to(migrated["project"] / ".cad/ai/measurement-results")
    else:
        path = migrated["transaction"] / "inventory.json"
        inventory = json.loads(path.read_bytes())
        inventory["locations"] = []
        path.write_text(json.dumps(inventory))
    with pytest.raises((ValueError, ResultError, OSError)):
        index.material(migrated["key"])


def test_legacy_material_reader_remains_available(migrated):
    assert index_for(migrated, ".cad").material(migrated["key"]) == migrated["snapshot"]
