"""Migration admission using real history produced by the exact pinned native CLI."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3

import pytest

from sico.migration.audit import audit
from sico.migration.prepare import prepare
from test_agent_codex_native import (
    gateway as gateway_fixture,
    native_codex as native_codex_fixture,
    test_native_app_server_mcp_continuation_and_resume as native_run,
)

RELATIVE = ".cad/ai/agent/sessions/native/codex"


@pytest.fixture(scope="module", params=["0.154.0", "0.156.1", "0.162.1"])
def native_sample(tmp_path_factory, request):
    from unittest.mock import patch
    version = request.param
    executable = ({"0.154.0": os.environ.get("CAD_TEST_PREVIOUS_CODEX_CLI"),
                   "0.156.1": os.environ.get("CAD_TEST_CODEX_0156_CLI")}.get(version)
                  if version != "0.162.1" else native_codex_fixture.__wrapped__())
    if not executable:
        pytest.skip("Set CAD_TEST_PREVIOUS_CODEX_CLI for independent legacy qualification")
    lock = Path(__file__).resolve().parents[2] / "terminal/codex-runtime.lock"
    pins = dict(line.split("=", 1) for line in lock.read_text().splitlines()
                if "=" in line and not line.startswith("#"))
    expected = {"0.154.0": "3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022",
                "0.156.1": "0b2e9301d6100dddda3b9d5c80ebaeaa3a2f1962388f2f36f6b96a9f08b1f33f"}.get(
                    version, pins["binary_sha256"])
    assert hashlib.sha256(Path(executable).read_bytes()).hexdigest() == expected
    project = tmp_path_factory.mktemp("native-source")
    gateway = gateway_fixture.__wrapped__()
    server = next(gateway)
    try:
        with patch("cadai.codex_selection.CODEX_VERSION", version), \
                patch("sico.codex.settings.CODEX_VERSION", version), \
                patch("sico.codex.thread_startup.CODEX_VERSION", version):
            native_run(project, executable, server, "native")
    finally:
        next(gateway, None)
    # A stopped 0.154 process can leave its own argv helpers behind. Build a
    # clean historical sample, verifying their exact identity and unlocked state;
    # production migration must still reject arbitrary scratch or symlink data.
    scratch = project / ".sico/ai/agent/sessions/native/codex/tmp/arg0"
    helpers = {"apply_patch", "applypatch", "codex-linux-sandbox", "codex-execve-wrapper"}
    for directory in scratch.glob("codex-arg0*"):
        assert directory.is_dir() and not directory.is_symlink()
        assert {path.name for path in directory.iterdir()} == helpers | {".lock"}
        for name in helpers:
            assert (directory / name).is_symlink()
            assert (directory / name).readlink() == Path(executable)
        lock = directory / ".lock"
        assert not lock.is_symlink() and lock.read_bytes() == b""
        with lock.open("rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            shutil.rmtree(directory)
    # Place completed fixture records in a historical project; production writers
    # remain closed to .cad. Only the absolute rollout location needs relocation.
    (project / ".sico").rename(project / ".cad")
    with sqlite3.connect(project / RELATIVE / "state_5.sqlite") as connection:
        connection.execute("UPDATE threads SET rollout_path=replace(rollout_path, ?, ?)",
                           (str(project / ".sico"), str(project / ".cad")))
    return project


@pytest.fixture
def project(native_sample, tmp_path, monkeypatch):
    observe = lambda _source: {"scope": "local", "referencing_pids": [], "unreadable_processes": 0}
    monkeypatch.setattr("sico.migration.audit.scan_local", observe)
    monkeypatch.setattr("sico.migration.prepare.scan_local", observe)
    shutil.copytree(native_sample / ".cad", tmp_path / ".cad")
    # The sample copy owns its own absolute location. Historical text stays intact.
    path = tmp_path / RELATIVE / "state_5.sqlite"
    connection = sqlite3.connect(path)
    try:
        connection.execute("UPDATE threads SET rollout_path=replace(rollout_path, ?, ?)",
                           (str(native_sample), str(tmp_path)))
        connection.commit()
    finally:
        connection.close()
    return tmp_path


def mutate(project, database, statement):
    connection = sqlite3.connect(project / RELATIVE / database)
    try:
        connection.execute(statement)
        connection.commit()
    finally:
        connection.close()


def assert_blocked(project):
    report = audit(project)
    assert report["blockers"]
    assert not report["eligible_for_stopped_migration"]
    assert not (project / ".sico").exists()
    return {row["code"] for row in report["blockers"]}


def test_native_history_is_read_only_and_never_staged_as_execution_home(project):
    home = project / RELATIVE
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
              for path in home.rglob("*") if path.is_file()}
    report = audit(project)
    assert not report["blockers"], report["blockers"]
    assert audit(project)["inventory_sha256"] == report["inventory_sha256"]
    result = prepare(project, writers_stopped=True, expected_inventory=report["inventory_sha256"])
    transaction = Path(result["transaction"])
    assert not (transaction / "staged/ai/agent/sessions/native/codex").exists()
    saved = transaction / "backup/ai/agent/sessions/native/codex"
    assert (saved / "state_5.sqlite").read_bytes() == (home / "state_5.sqlite").read_bytes()
    assert not (saved / "thread-writer-locks").exists()
    assert not (saved / "skills").exists()
    locations = json.loads((transaction / "inventory.json").read_bytes())["locations"]
    assert next(row for row in locations if row["source"] == str(home / "state_5.sqlite"))[
        "target"] == str(saved / "state_5.sqlite")
    assert prepare(project, writers_stopped=True,
                   expected_inventory=report["inventory_sha256"])["transaction"] == str(transaction)
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
                      for path in before}


@pytest.mark.parametrize("database,statement", [
    ("state_5.sqlite", "CREATE TABLE unexpected (value TEXT)"),
    ("state_5.sqlite", "UPDATE _sqlx_migrations SET success=0 WHERE version=1"),
    ("state_5.sqlite", "UPDATE threads SET rollout_path='/outside/rollout.jsonl'"),
    ("state_5.sqlite", "UPDATE threads SET cli_version='0.155.0'"),
    ("state_5.sqlite", "UPDATE backfill_state SET status='pending'"),
    ("state_5.sqlite", "INSERT INTO thread_spawn_edges VALUES ('parent', 'child', 'completed')"),
    ("queue_1.sqlite", "INSERT INTO queued_items VALUES ('q', 't', '{}', 1, 0, 0)"),
    ("goals_1.sqlite", "INSERT INTO thread_goals VALUES ('t','g','goal','active',NULL,0,0,0,0)"),
    ("memories_1.sqlite", "INSERT INTO jobs(kind,job_key,status,retry_remaining) "
                          "VALUES ('k','j','running',0)"),
    ("thread_history_1.sqlite", "UPDATE thread_turns SET status='inProgress'"),
    ("thread_history_1.sqlite", "UPDATE thread_turns SET rollout_byte_offset=0"),
    ("thread_history_1.sqlite", "DELETE FROM thread_items"),
    ("thread_history_1.sqlite", "UPDATE thread_history_projection_state "
                                "SET next_rollout_byte_offset=0"),
])
def test_unqualified_or_pending_database_blocks(project, database, statement):
    mutate(project, database, statement)
    assert "invalid_or_unresolved_native_history" in assert_blocked(project)


@pytest.mark.parametrize("damage", ["partial", "ordinal", "identity", "pending", "orphan", "binding",
                                   "missing-output", "unknown-item", "running-item", "missing-receipt"])
def test_rollout_and_task_binding_damage_blocks(project, damage):
    path = next((project / RELATIVE / "sessions").rglob("*.jsonl"))
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    if damage == "partial":
        with path.open("ab") as stream:
            stream.write(b'{"partial":')
    elif damage == "orphan":
        shutil.copyfile(path, path.with_name(path.name.replace(path.stem[-36:], "a" * 8 + "-" +
                        "a" * 4 + "-" + "a" * 4 + "-" + "a" * 4 + "-" + "a" * 12)))
    elif damage == "binding":
        path = project / ".cad/ai/agent/sessions/native/events.jsonl"
        rows = [json.loads(line) for line in path.read_bytes().splitlines()]
        for row in rows:
            if row["kind"] == "codex.turn.bound":
                row["payload"]["native"]["turn_id"] = "unknown"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    else:
        if damage == "ordinal":
            rows[-1]["ordinal"] += 1
        elif damage == "identity":
            rows[0]["payload"]["id"] = "unknown"
        elif damage == "missing-output":
            for row in rows:
                if row["payload"].get("type") == "function_call_output":
                    row["payload"] = {"type": "message"}
        elif damage in {"unknown-item", "running-item", "missing-receipt"}:
            item = next(row["payload"]["item"] for row in rows
                        if row["payload"].get("type") == "item_completed"
                        and row["payload"]["item"]["type"] == "McpToolCall")
            if damage == "missing-receipt":
                item["type"] = "AgentMessage"
            else:
                item["type" if damage == "unknown-item" else "status"] = "unknown"
        else:
            rows = rows[:-1]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert "invalid_or_unresolved_native_history" in assert_blocked(project)


@pytest.mark.parametrize("damage", ["home-mode", "child-owner-mode", "asset", "extra",
                                   "symlink", "hardlink", "writer", "scratch"])
def test_native_boundary_remains_private_and_reviewed(project, damage):
    home = project / RELATIVE
    if damage == "home-mode":
        home.chmod(0o750)
    elif damage == "child-owner-mode":
        (home / "state_5.sqlite").chmod(0o666)
    elif damage == "asset":
        (home / "skills/.system/skill-creator/SKILL.md").write_text("custom content")
    elif damage == "extra":
        (home / "unknown.json").write_text("{}")
    elif damage == "symlink":
        path = home / "session_index.jsonl"
        path.unlink()
        path.symlink_to(home / "state_5.sqlite")
    elif damage == "hardlink":
        os.link(home / "state_5.sqlite", project / "external-link")
    elif damage == "scratch":
        (home / "tmp/runtime-leftover").mkdir(mode=0o700)
    else:
        with (home / "thread-writer-locks/.coordination.lock").open("rb") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert "active_writer" in assert_blocked(project)
        return
    assert_blocked(project)


@pytest.mark.parametrize("damage", ["attachment", "consolidation", "missing-consolidation", "mixed"])
def test_new_native_state_requires_independent_reconciliation(project, damage):
    database = project / RELATIVE / "state_5.sqlite"
    with sqlite3.connect(database) as connection:
        version = connection.execute("SELECT cli_version FROM threads LIMIT 1").fetchone()[0]
        identity = connection.execute("SELECT id FROM threads LIMIT 1").fetchone()[0]
    if damage == "mixed":
        with sqlite3.connect(database) as connection:
            columns = [r[1] for r in connection.execute("PRAGMA table_info(threads)")]
            row = list(connection.execute("SELECT * FROM threads LIMIT 1").fetchone())
            row[columns.index("id")] = "mixed-version"
            row[columns.index("cli_version")] = "0.154.0" if version == "0.156.1" else "0.156.1"
            connection.execute("INSERT INTO threads VALUES (" + ",".join("?" for _ in row) + ")", row)
    elif version == "0.154.0":
        # Old profile must reject the new shape instead of acquiring new qualification.
        mutate(project, "memories_1.sqlite", "CREATE TABLE consolidation_progress (value TEXT)")
    elif damage == "attachment":
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO thread_attachments VALUES (?,?,?,?,?,?)",
                               ("asset", identity, "unknown", "key", "{}", 0))
    elif damage == "consolidation":
        mutate(project, "memories_1.sqlite", "UPDATE consolidation_progress SET max_thread_count=1")
    else:
        mutate(project, "memories_1.sqlite", "DELETE FROM consolidation_progress")
    assert "invalid_or_unresolved_native_history" in assert_blocked(project)
