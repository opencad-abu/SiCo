"""Only sealed, reconciled router evidence can enter a migration transaction."""

import fcntl
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from sico.migration.audit import audit
from sico.migration.prepare import prepare
from sico.transport.router_archive import maintain_router_logs
from test_agent_bridge import bound
from test_agent_router_archive import fingerprints, retired as current_retired
from legacy_state_fixtures import legacy_fixture

from sico.transport.instance_bridge import InstanceBridge
from sico.transport.router_journal import record_digest


@legacy_fixture
def retired(root, generation="g", state="completed"):
    (root / ".cad").mkdir(mode=0o700, exist_ok=True)
    return current_retired(root, generation, state)


@pytest.fixture(autouse=True)
def isolated_process_observation(monkeypatch):
    for module in ("audit", "prepare"):
        monkeypatch.setattr("sico.migration." + module + ".scan_local", lambda source: {
            "scope": "local", "referencing_pids": [], "unreadable_processes": 0})


@pytest.mark.parametrize("archived", [False, True])
def test_sealed_router_is_copied_without_live_descriptor(tmp_path, archived):
    descriptor = retired(tmp_path)
    if archived:
        assert not maintain_router_logs(tmp_path, apply=True, retention_days=0)["errors"]
    before = fingerprints(tmp_path / ".cad")
    report = audit(tmp_path)
    assert not report["blockers"]
    result = prepare(tmp_path, expected_inventory=report["inventory_sha256"], writers_stopped=True)
    transaction = Path(result["transaction"])
    relative = descriptor.relative_to(tmp_path / ".cad")
    assert (transaction / "backup" / relative).read_bytes() == descriptor.read_bytes()
    assert not (transaction / "staged" / relative).exists()
    assert not list((transaction / "staged").rglob("*.lock"))
    assert fingerprints(tmp_path / ".cad") == before
    assert prepare(tmp_path, expected_inventory=report["inventory_sha256"],
                   writers_stopped=True) == result


@pytest.mark.parametrize("damage", ["unsealed", "pending", "partial", "source", "seal",
                                     "descriptor", "orphan", "archive", "extra", "pruned"])
def test_uncertain_router_evidence_blocks_without_repair(tmp_path, damage):
    path = retired(tmp_path, state="running" if damage == "pending" else "completed")
    seal_path = path.with_suffix(".retired.json")
    if damage == "unsealed":
        seal_path.unlink()
    elif damage in {"partial", "source"}:
        source = path.with_suffix(".state") / "requests.jsonl"
        with source.open("ab") as stream:
            stream.write(b'{"incomplete":' if damage == "partial" else b"{}\n")
    elif damage == "seal":
        row = json.loads(seal_path.read_bytes())
        row["identity"]["generation"] = "another"
        seal_path.write_text(json.dumps(row))
    elif damage == "descriptor":
        row = json.loads(path.read_bytes())
        row["generation"] = "another"
        path.write_text(json.dumps(row))
    elif damage == "orphan":
        path.unlink()
    elif damage in {"archive", "extra", "pruned"}:
        assert not maintain_router_logs(tmp_path, apply=True, retention_days=0)["errors"]
        archive = path.with_suffix(".state") / "archive"
        target = archive / ("requests.jsonl.gz" if damage == "archive" else
                            "relay.jsonl.gz" if damage == "extra" else "skill.jsonl.gz")
        target.write_bytes(b"corrupt or unexpected")
        target.chmod(0o600)
    before = fingerprints(tmp_path / ".cad")
    report = audit(tmp_path)
    assert any(row["code"] == "invalid_or_unresolved_router_history" for row in report["blockers"])
    assert fingerprints(tmp_path / ".cad") == before


@pytest.mark.parametrize("suffix", [".lock", ".state/writer.lock", ".state/maintenance.lock"])
def test_held_router_lock_blocks(tmp_path, suffix):
    path = retired(tmp_path)
    lock = Path(str(path.with_suffix("")) + suffix)
    with lock.open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert any(row["code"] == "active_writer" for row in audit(tmp_path)["blockers"])


def test_live_bridge_and_completed_write_stay_blocked(tmp_path):
    with InstanceBridge(tmp_path, bound()) as bridge:
        bridge.journal.record(dict(request_id="a" * 32, session_id="owner", target_id="bound",
                                   method="assistant_call", state="completed", started_at=1,
                                   reply={"id": "a" * 32, "ok": True}))
        assert audit(tmp_path)["blockers"]
    assert audit(tmp_path)["blockers"]


@pytest.mark.parametrize("damage", [None, "content", "pruned"])
def test_archive_and_remaining_source_must_agree(tmp_path, monkeypatch, damage):
    path = retired(tmp_path)
    monkeypatch.setattr("sico.transport.router_archive._remove_sources", lambda *args: None)
    assert not maintain_router_logs(tmp_path, apply=True, retention_days=365)["errors"]
    archive = path.with_suffix(".state") / "archive"
    manifest_path = archive / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    if damage == "content":
        # A self-consistent archive must still match the independently sealed source.
        payload = path.with_suffix(".skill.jsonl").read_bytes().replace(b"test", b"fake")
        (archive / "skill.jsonl.gz").write_bytes(gzip.compress(payload))
        manifest["files"]["skill.jsonl"]["sha256"] = hashlib.sha256(payload).hexdigest()
    elif damage == "pruned":
        (archive / "skill.jsonl.gz").unlink()
        manifest["files"]["skill.jsonl"]["status"] = "pruned"
    if damage is not None:
        manifest.pop("sha256")
        manifest["sha256"] = record_digest(manifest)
        manifest_path.write_text(json.dumps(manifest))
    before = fingerprints(tmp_path / ".cad")
    blockers = audit(tmp_path)["blockers"]
    assert bool(blockers) == (damage is not None)
    assert fingerprints(tmp_path / ".cad") == before
