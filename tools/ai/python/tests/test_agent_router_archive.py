"""Retired-generation maintenance never touches an active writer or replays work."""

import fcntl
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_agent_bridge import bound

from sico.core.contracts import NeedsReconcile
from sico.transport.bridge_client import BridgeClient
from sico.transport.instance_bridge import InstanceBridge
from sico.transport.router_archive import maintain_router_logs
from sico.transport.router_journal import RouterJournal


def private_file(path, data):
    path.write_bytes(data)
    path.chmod(0o600)


def retired(root, generation="g", state="completed"):
    with InstanceBridge(root, bound(generation)) as bridge:
        bridge.journal.record(
            dict(
                request_id="a" * 32,
                session_id="owner",
                target_id="bound",
                method="get_context",
                state=state,
                started_at=1,
                reply={"id": "a" * 32, "ok": True, "result": {"value": "saved"}}
                if state == "completed"
                else None,
            )
        )
        private_file(bridge.path.with_suffix(".skill.jsonl"), b'{"stage":"test"}\n' * 1024)
        path = bridge.path
    return path


def fingerprints(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def maintain(root, **kwargs):
    return maintain_router_logs(root, **kwargs)


def test_dry_run_and_live_bridge_do_not_change_files(tmp_path):
    retired(tmp_path)
    with InstanceBridge(tmp_path, bound("live")) as live:
        before = fingerprints(tmp_path)
        report = maintain(tmp_path, retention_days=0, max_bytes=1)
        assert report["dry_run"]
        assert report["would_prune_files"]
        assert report["reclaimable_diagnostic_bytes"] > 0
        assert fingerprints(tmp_path) == before
        assert {r["status"] for r in report["generations"]} == {"active", "would_archive"}
        active_before = {
            p.name: p.read_bytes()
            for p in live.path.parent.glob(live.path.stem + "*")
            if p.is_file()
        }
        report = maintain(tmp_path, apply=True, retention_days=0, max_bytes=1)
        assert report["over_budget"]
        assert active_before == {
            p.name: p.read_bytes()
            for p in live.path.parent.glob(live.path.stem + "*")
            if p.is_file()
        }


def test_compressed_receipt_and_tombstone_survive_diagnostic_pruning(tmp_path):
    path = retired(tmp_path)
    before = BridgeClient.archived_receipt(tmp_path, bound(), "a" * 32, session_id="owner")
    report = maintain(tmp_path, apply=True, retention_days=0)
    assert not report["errors"]
    assert not path.with_suffix(".state").joinpath("requests.jsonl").exists()
    archive = path.with_suffix(".state") / "archive"
    assert (archive / "requests.jsonl.gz").exists()
    assert (archive / "bindings.jsonl.gz").exists()
    assert not (archive / "skill.jsonl.gz").exists()
    manifest = json.loads((archive / "manifest.json").read_text())
    assert manifest["files"]["skill.jsonl"]["status"] == "pruned"
    assert BridgeClient.archived_receipt(tmp_path, bound(), "a" * 32, session_id="owner") == before
    with pytest.raises(NeedsReconcile, match="owner"):
        BridgeClient.archived_receipt(tmp_path, bound(), "a" * 32, session_id="another")
    with pytest.raises(RuntimeError, match="generation"):
        InstanceBridge(tmp_path, bound())
    snapshot = fingerprints(tmp_path)
    maintain(tmp_path, apply=True, retention_days=0)
    assert fingerprints(tmp_path) == snapshot


def test_unknown_evidence_is_pinned_even_over_budget(tmp_path):
    path = retired(tmp_path, state="timed_out_unknown")
    report = maintain(tmp_path, apply=True, retention_days=0, max_bytes=1)
    assert report["over_budget"] and not report["errors"]
    assert report["generations"][0]["pinned"]
    assert (path.with_suffix(".state") / "archive/skill.jsonl.gz").exists()
    row = BridgeClient.archived_receipt(tmp_path, bound(), "a" * 32, session_id="owner")
    assert row["state"] == "timed_out_unknown" and row["automatic_resume_allowed"] is False


def test_unsealed_or_still_dispatching_generation_is_not_archived(tmp_path):
    path = retired(tmp_path)
    path.with_suffix(".retired.json").unlink()
    with InstanceBridge(tmp_path, bound("pending")) as bridge:
        bridge.router._skill_pending = "b" * 32
        pending = bridge.path
    assert not pending.with_suffix(".retired.json").exists()
    before = fingerprints(tmp_path)
    report = maintain(tmp_path, apply=True, retention_days=0)
    assert {r["status"] for r in report["generations"]} == {"unsealed"}
    assert fingerprints(tmp_path) == before


def test_reader_and_writer_locks_prevent_archive(tmp_path):
    path = retired(tmp_path)
    descriptor = json.loads(path.read_text())
    identity = {k: descriptor[k] for k in ("instance_id", "generation", "bridge_id", "router_id")}
    with RouterJournal(path.with_suffix(".state"), **identity, readonly=True):
        assert maintain(tmp_path, apply=True)["generations"][0]["status"] == "reader_active"
    with (path.with_suffix(".state") / "writer.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert maintain(tmp_path, apply=True)["generations"][0]["status"] == "writer_active"


@pytest.mark.parametrize("damage", ["journal", "seal", "symlink", "changed"])
def test_invalid_evidence_is_preserved(tmp_path, damage):
    path = retired(tmp_path)
    journal = path.with_suffix(".state") / "requests.jsonl"
    if damage == "journal":
        data = journal.read_bytes().replace(b'"sha256": "', b'"sha256": "0', 1)
        journal.write_bytes(data)
    elif damage == "seal":
        seal = path.with_suffix(".retired.json")
        data = json.loads(seal.read_text())
        data["identity"]["generation"] = "another"
        seal.write_text(json.dumps(data))
    elif damage == "symlink":
        journal.rename(journal.with_suffix(".saved"))
        journal.symlink_to(journal.with_suffix(".saved"))
    else:
        with path.with_suffix(".skill.jsonl").open("ab") as stream:
            stream.write(b"late evidence\n")
    before = fingerprints(tmp_path)
    report = maintain(tmp_path, apply=True, retention_days=0)
    assert report["errors"]
    assert fingerprints(tmp_path) == before


def test_capacity_prunes_oldest_diagnostics_but_keeps_receipts(tmp_path):
    old = retired(tmp_path, "old")
    new = retired(tmp_path, "new")
    first = maintain(tmp_path, apply=True, retention_days=365)
    archive = old.with_suffix(".state") / "archive"
    size = sum(
        p.stat().st_size
        for p in archive.glob("*.gz")
        if p.name not in {"requests.jsonl.gz", "bindings.jsonl.gz"}
    )
    second = maintain(
        tmp_path, apply=True, retention_days=365, max_bytes=first["bytes_after"] - size + 512
    )
    # Use a strict budget as well: protected receipt bytes can exceed the target.
    if not second["pruned_files"]:
        second = maintain(tmp_path, apply=True, retention_days=365, max_bytes=1)
    assert second["pruned_files"][0]["generation"] == "old"
    for path, generation in ((old, "old"), (new, "new")):
        assert (path.with_suffix(".state") / "archive/requests.jsonl.gz").exists()
        assert (
            BridgeClient.archived_receipt(
                tmp_path, bound(generation), "a" * 32, session_id="owner"
            )["state"]
            == "completed"
        )


def test_cli_dry_run_and_receipt_after_archive(tmp_path):
    retired(tmp_path)
    entry = Path(__file__).absolute().parents[3] / "sico/python/sico-entry"
    command = [sys.executable, str(entry), "router-maintenance", "--launch-dir", str(tmp_path)]
    environment = os.environ.copy()
    environment.pop("CAD_HOME", None)
    environment["SICO_HOME"] = str(Path(__file__).resolve().parents[4])
    result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=10, env=environment)
    assert json.loads(result.stdout)["dry_run"]
    subprocess.run(command + ["--apply"], capture_output=True, text=True, check=True, timeout=10, env=environment)
    result = subprocess.run(
        [
            sys.executable,
            str(entry),
            "router-receipt",
            "--launch-dir",
            str(tmp_path),
            "--instance",
            bound().instance_id,
            "--generation",
            "g",
            "--session",
            "owner",
            "--request",
            "a" * 32,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
        env=environment,
    )
    assert json.loads(result.stdout)["reply"]["result"]["value"] == "saved"


def test_compressed_corruption_is_rejected(tmp_path):
    path = retired(tmp_path)
    maintain(tmp_path, apply=True)
    archive = path.with_suffix(".state") / "archive/requests.jsonl.gz"
    archive.write_bytes(archive.read_bytes()[:-8])
    with pytest.raises((OSError, ValueError, EOFError)):
        BridgeClient.archived_receipt(tmp_path, bound(), "a" * 32, session_id="owner")
    before = fingerprints(tmp_path)
    assert maintain(tmp_path, apply=True, retention_days=0)["errors"]
    assert fingerprints(tmp_path) == before


@pytest.mark.parametrize("boundary", ["compress", "published", "prune"])
def test_interrupted_maintenance_keeps_receipt_and_can_finish(tmp_path, monkeypatch, boundary):
    from sico.transport import router_archive as module

    path = retired(tmp_path)
    before = BridgeClient.archived_receipt(tmp_path, bound(), "a" * 32, session_id="owner")
    if boundary == "compress":
        name = "_compress"
    elif boundary == "published":
        name = "_remove_sources"
    else:
        maintain(tmp_path, apply=True)
        name = "_finish_pruning"
    original = getattr(module, name)

    def fail(*args):
        if boundary == "prune" and not any(
            row["status"] == "pruned" for row in args[1]["files"].values()
        ):
            return original(*args)
        raise OSError("simulated storage interruption")

    monkeypatch.setattr(module, name, fail)
    report = maintain(tmp_path, apply=True, retention_days=0)
    assert report["errors"]
    assert BridgeClient.archived_receipt(tmp_path, bound(), "a" * 32, session_id="owner") == before
    if boundary != "prune":
        assert (path.with_suffix(".state") / "requests.jsonl").exists()
    monkeypatch.setattr(module, name, original)
    report = maintain(tmp_path, apply=True, retention_days=0)
    assert not report["errors"]
    assert BridgeClient.archived_receipt(tmp_path, bound(), "a" * 32, session_id="owner") == before
    assert not (path.with_suffix(".state") / "requests.jsonl").exists()


def test_completed_assistant_call_keeps_diagnostics_for_business_reconciliation(tmp_path):
    with InstanceBridge(tmp_path, bound()) as bridge:
        bridge.journal.record(
            dict(
                request_id="a" * 32,
                session_id="owner",
                target_id="bound",
                method="assistant_call",
                state="completed",
                started_at=1,
                reply={"id": "a" * 32, "ok": True},
            )
        )
        private_file(bridge.path.with_suffix(".skill.jsonl"), b"private evidence\n")
        path = bridge.path
    report = maintain(tmp_path, apply=True, retention_days=0, max_bytes=1)
    assert report["generations"][0]["pinned"]
    assert (path.with_suffix(".state") / "archive/skill.jsonl.gz").exists()


def test_simultaneous_maintenance_and_query_never_lose_receipts(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    retired(tmp_path)
    with ThreadPoolExecutor(3) as pool:
        pending = [pool.submit(maintain, tmp_path, apply=True, retention_days=0) for _ in range(2)]
        for result in pending:
            assert not result.result(10)["errors"]
    assert (
        BridgeClient.archived_receipt(tmp_path, bound(), "a" * 32, session_id="owner")["state"]
        == "completed"
    )


def test_archive_destination_symlink_is_rejected(tmp_path):
    path = retired(tmp_path)
    outside = tmp_path / "external"
    outside.mkdir(mode=0o700)
    (path.with_suffix(".state") / "archive").symlink_to(outside, target_is_directory=True)
    before = fingerprints(tmp_path)
    assert maintain(tmp_path, apply=True)["errors"]
    assert fingerprints(tmp_path) == before
    assert not list(outside.iterdir())


def test_unsealed_native_generation_requires_confirmed_host_exit(tmp_path):
    from sico.transport.router_archive import capture_archive_owner

    owner = capture_archive_owner("v" + str(os.getpid()), allow_self=True)
    assert owner
    with InstanceBridge(tmp_path, bound(), archive_owner=owner) as bridge:
        bridge.journal.record(
            dict(
                request_id="a" * 32,
                session_id="owner",
                target_id="bound",
                method="get_context",
                state="running",
            )
        )
        path = bridge.path
    path.with_suffix(".retired.json").unlink()
    before = fingerprints(tmp_path)
    assert maintain(tmp_path, apply=True)["generations"][0]["status"] == "host_active"
    assert fingerprints(tmp_path) == before
    descriptor = json.loads(path.read_text())
    descriptor["archive_owner"]["process"]["start"] = "0"
    path.write_text(json.dumps(descriptor))
    before = fingerprints(tmp_path)
    assert maintain(tmp_path)["generations"][0]["status"] == "would_archive"
    assert fingerprints(tmp_path) == before
    report = maintain(tmp_path, apply=True)
    assert report["generations"][0]["pinned"] and not report["errors"]
    receipt = BridgeClient.archived_receipt(tmp_path, bound(), "a" * 32, session_id="owner")
    assert receipt["state"] == "timed_out_unknown"
    assert receipt["automatic_resume_allowed"] is False


def test_host_identity_must_match_current_process_ancestry():
    from sico.transport.router_archive import capture_archive_owner

    assert capture_archive_owner("v" + str(os.getppid()))
    assert capture_archive_owner("v" + str(os.getpid())) is None
    assert capture_archive_owner("v999999999") is None
    assert capture_archive_owner("bridge-instance") is None


def test_incomplete_tail_and_full_event_sequence_are_preserved(tmp_path):
    import gzip

    with InstanceBridge(tmp_path, bound()) as bridge:
        bridge.journal.record(
            dict(
                request_id="a" * 32,
                session_id="owner",
                target_id="bound",
                method="get_context",
                state="accepted",
            )
        )
        bridge.journal.record(dict(request_id="a" * 32, state="running", started_at=1))
        path = bridge.path
        source = path.with_suffix(".state") / "requests.jsonl"
        with source.open("ab") as stream:
            stream.write(b'{"incomplete":')
    original = source.read_bytes()
    assert not maintain(tmp_path, apply=True)["errors"]
    assert (
        gzip.decompress((path.with_suffix(".state") / "archive/requests.jsonl.gz").read_bytes())
        == original
    )
    receipt = BridgeClient.archived_receipt(tmp_path, bound(), "a" * 32, session_id="owner")
    assert receipt["state"] == "timed_out_unknown"


def test_old_journal_without_maintenance_lock_remains_readable(tmp_path):
    path = retired(tmp_path)
    path.with_suffix(".retired.json").unlink()
    (path.with_suffix(".state") / "maintenance.lock").unlink()
    assert (
        BridgeClient.archived_receipt(tmp_path, bound(), "a" * 32, session_id="owner")["state"]
        == "completed"
    )
    assert maintain(tmp_path, apply=True)["generations"][0]["status"] == "unsealed"
