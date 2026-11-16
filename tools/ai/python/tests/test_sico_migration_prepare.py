"""Stopped preparation backs up exact bytes and safely resumes interrupted copies."""

import json
from pathlib import Path

import pytest

from sico.core.contracts import BoundContext, RunState
from sico.migration.audit import audit
from sico.migration.prepare import prepare
from legacy_state_fixtures import HistoricalJournal as SessionJournal


@pytest.fixture(autouse=True)
def isolated_process_observation(monkeypatch):
    observe = lambda source: {"scope": "local", "referencing_pids": [], "unreadable_processes": 0}
    monkeypatch.setattr("sico.migration.audit.scan_local", observe)
    monkeypatch.setattr("sico.migration.prepare.scan_local", observe)


def legacy(root):
    (root / ".cad").mkdir(mode=0o700, exist_ok=True)
    with SessionJournal(root, "session") as journal:
        reference = journal.text_attachment("old absolute paths must remain verbatim")
        journal.append("test", {"artifact": reference, "historical": str(root / ".cad")},
                       RunState(BoundContext("i", "g", "t", {})))
    return audit(root)["inventory_sha256"]


def test_prepare_retains_exact_backup_and_external_path_mapping(tmp_path):
    digest = legacy(tmp_path)
    before = {str(path.relative_to(tmp_path / ".cad")): path.read_bytes()
              for path in (tmp_path / ".cad").rglob("*") if path.is_file()}
    result = prepare(tmp_path, expected_inventory=digest, writers_stopped=True)
    transaction = Path(result["transaction"])
    assert result["status"] == "prepared" and result["cutover"] is False
    assert not (tmp_path / ".sico").exists()
    assert {str(path.relative_to(tmp_path / ".cad")): path.read_bytes()
            for path in (tmp_path / ".cad").rglob("*") if path.is_file()} == before
    inventory = json.loads((transaction / "inventory.json").read_bytes())
    for row in inventory["entries"]:
        if row["action"] != "retain":
            continue
        assert (transaction / "backup" / row["path"]).read_bytes() == before[row["path"]]
        assert (transaction / "staged" / row["path"]).read_bytes() == before[row["path"]]
    assert not list((transaction / "staged").rglob("*.lock"))
    assert inventory["locations"]
    assert prepare(tmp_path, expected_inventory=digest, writers_stopped=True) == result


def test_shutdown_and_fingerprint_are_required_before_writes(tmp_path):
    digest = legacy(tmp_path)
    with pytest.raises(ValueError, match="shutdown"):
        prepare(tmp_path, expected_inventory=digest)
    with pytest.raises(ValueError, match="reviewed inventory"):
        prepare(tmp_path, expected_inventory="0" * 64, writers_stopped=True)
    assert not (tmp_path / ".sico-migration").exists()


def test_interrupted_copy_resumes_with_identical_content(tmp_path, monkeypatch):
    import sico.migration.prepare as module

    digest = legacy(tmp_path)
    original = module.copy_entry
    count = 0

    def interrupted(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("simulated power loss")
        return original(*args)

    monkeypatch.setattr(module, "copy_entry", interrupted)
    with pytest.raises(OSError, match="power loss"):
        prepare(tmp_path, expected_inventory=digest, writers_stopped=True)
    assert not (tmp_path / ".sico").exists()
    monkeypatch.setattr(module, "copy_entry", original)
    result = prepare(tmp_path, expected_inventory=digest, writers_stopped=True)
    assert result["status"] == "prepared"


@pytest.mark.parametrize("damage", ["backup", "staged", "extra"])
def test_retry_refuses_corrupted_or_extra_transaction_data(tmp_path, damage):
    digest = legacy(tmp_path)
    result = prepare(tmp_path, expected_inventory=digest, writers_stopped=True)
    transaction = Path(result["transaction"])
    if damage == "extra":
        (transaction / "staged/uninventoried").write_text("unexpected")
    else:
        next((transaction / damage).rglob("events.jsonl")).write_text("changed")
    with pytest.raises(ValueError):
        prepare(tmp_path, expected_inventory=digest, writers_stopped=True)
    assert not (tmp_path / ".sico").exists()


def test_source_mutation_during_copy_prevents_prepared_receipt(tmp_path, monkeypatch):
    import sico.migration.prepare as module

    digest = legacy(tmp_path)
    original = module.copy_entry
    changed = False

    def mutate(tree, entry, destination):
        nonlocal changed
        original(tree, entry, destination)
        if not changed:
            changed = True
            source = tmp_path / ".cad" / entry["path"]
            source.write_bytes(source.read_bytes() + b"changed")

    monkeypatch.setattr(module, "copy_entry", mutate)
    with pytest.raises(ValueError):
        prepare(tmp_path, expected_inventory=digest, writers_stopped=True)
    record = tmp_path / ".sico-migration" / digest / "transaction.json"
    assert json.loads(record.read_bytes())["status"] == "copying"
    assert not (tmp_path / ".sico").exists()


def test_active_writer_cannot_prepare(tmp_path):
    digest = legacy(tmp_path)
    with SessionJournal(tmp_path, "session"):
        with pytest.raises(ValueError, match="blocked"):
            prepare(tmp_path, expected_inventory=digest, writers_stopped=True)
    assert not (tmp_path / ".sico-migration").exists()


def test_process_uncertainty_stops_before_copy(tmp_path, monkeypatch):
    digest = legacy(tmp_path)
    monkeypatch.setattr("sico.migration.prepare.scan_local", lambda source: {
        "scope": "local", "referencing_pids": [], "unreadable_processes": 1})
    with pytest.raises(ValueError, match="shutdown"):
        prepare(tmp_path, expected_inventory=digest, writers_stopped=True)
    assert not (tmp_path / ".sico-migration" / digest).exists()


def test_interrupted_partial_file_is_recreated_without_reusing_other_links(tmp_path):
    import hashlib

    digest = legacy(tmp_path)
    result = prepare(tmp_path, expected_inventory=digest, writers_stopped=True)
    backup = Path(result["transaction"]) / "backup"
    target = next(backup.rglob("events.jsonl"))
    relative = str(target.relative_to(backup))
    temporary = target.parent / (".copy-" + hashlib.sha256(relative.encode()).hexdigest())
    target.unlink()
    temporary.write_bytes(b"partial")
    temporary.chmod(0o600)
    assert prepare(tmp_path, expected_inventory=digest, writers_stopped=True) == result
    assert not temporary.exists()
    assert target.read_bytes() == (tmp_path / ".cad" / relative).read_bytes()


@pytest.mark.parametrize("filesystem,options", [
    ("nfs4", {"local_lock=flock"}), ("nfs", {"nolock"}), ("ext4", {"ro"})])
def test_unverified_locking_blocks_audit_and_preparation(tmp_path, monkeypatch, filesystem, options):
    digest = legacy(tmp_path)
    monkeypatch.setattr("sico.storage.project_locking.mount_policy",
                        lambda _path: (filesystem, options))
    assert "lock_policy_unverified" in {row["code"] for row in audit(tmp_path)["blockers"]}
    with pytest.raises(ValueError, match="blocked"):
        prepare(tmp_path, expected_inventory=digest, writers_stopped=True)
    assert not (tmp_path / ".sico-migration").exists()
