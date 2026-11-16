"""Native SQLite ownership, quiescence and projection checks."""

from __future__ import annotations

import json
import sqlite3

import pytest
from sico.migration import native_databases

THREAD = "01984de2-8f74-7c91-a3b2-5c5e937cf318"
VERSION = "0.156.1"

EMPTY_TABLES = (
    "thread_dynamic_tools", "thread_spawn_edges", "remote_control_enrollments",
    "external_agent_config_imports", "rollout_migration_state",
    "rollout_migration_skipped_rollouts", "projects", "project_roots",
    "project_idempotency_keys",
)


def database(*statements):
    value = sqlite3.connect(":memory:")
    for statement in statements:
        value.execute(statement)
    return value


def state_database(version=VERSION, *, thread=THREAD, cli=None, mode="paginated"):
    cli = version if cli is None else cli
    tables = [
        "CREATE TABLE backfill_state(status TEXT)",
        "INSERT INTO backfill_state VALUES ('complete')",
        "CREATE TABLE threads(id TEXT, rollout_path TEXT, cwd TEXT, "
        "cli_version TEXT, history_mode TEXT)",
    ]
    value = database(*tables)
    value.execute("INSERT INTO threads VALUES (?, ?, ?, ?, ?)",
                  (thread, "/home/" + thread + ".jsonl", "/home", cli, mode))
    for table in EMPTY_TABLES:
        value.execute("CREATE TABLE " + table + "(value TEXT)")
    attachment = "thread_artifacts" if version == "0.154.0" else "thread_attachments"
    value.execute("CREATE TABLE " + attachment + "(value TEXT)")
    if version == "0.162.1":
        value.execute("CREATE TABLE thread_sections(id TEXT, name TEXT, appearance TEXT)")
        value.execute("INSERT INTO thread_sections VALUES (?,?,?)",
                      ("01984de2-8f74-7c91-a3b2-5c5e937cf318", "Pinned", None))
        value.execute("ALTER TABLE threads ADD COLUMN is_pinned INTEGER DEFAULT 0")
        value.execute("ALTER TABLE threads ADD COLUMN thread_section_id TEXT")
        value.execute("ALTER TABLE threads ADD COLUMN project_id TEXT")
    return value


def test_records_and_empty_are_bounded():
    value = database("CREATE TABLE rows(value INTEGER)",
                     "INSERT INTO rows VALUES (1)", "INSERT INTO rows VALUES (2)",
                     "CREATE TABLE empty_table(value TEXT)")
    monkeypatch_limit = native_databases.MAX_ROWS
    native_databases.MAX_ROWS = 1
    try:
        with pytest.raises(ValueError, match="validation limit"):
            list(native_databases.records(value, "SELECT value FROM rows"))
    finally:
        native_databases.MAX_ROWS = monkeypatch_limit
    native_databases.empty(value, ("empty_table",))
    with pytest.raises(ValueError, match="reconciliation adapter"):
        native_databases.empty(value, ("rows",))


@pytest.mark.parametrize("version", ["0.154.0", "0.156.1", "0.162.1"])
def test_validate_state_accepts_supported_profiles(version):
    value = state_database(version)
    threads = native_databases.validate_state(value, version)
    assert threads[THREAD]["cwd"] == "/home"


@pytest.mark.parametrize("version,column,message", [
    ("0.156.1", "thread_attachments", "reconciliation adapter"),
    ("0.162.1", "thread_sections", "custom thread sections"),
])
def test_validate_state_rejects_new_unreviewed_state(version, column, message):
    value = state_database(version)
    if column == "thread_sections":
        value.execute("UPDATE thread_sections SET name='other'")
    else:
        value.execute("INSERT INTO thread_attachments VALUES ('x')")
    with pytest.raises(ValueError, match=message):
        native_databases.validate_state(value, version)


def test_validate_state_rejects_incomplete_backfill_bad_threads_and_empty_home():
    value = state_database()
    value.execute("UPDATE backfill_state SET status='pending'")
    with pytest.raises(ValueError, match="backfill"):
        native_databases.validate_state(value, VERSION)

    value = state_database()
    value.execute("UPDATE threads SET cli_version='old'")
    with pytest.raises(ValueError, match="profile"):
        native_databases.validate_state(value, VERSION)

    value = state_database()
    value.execute("UPDATE threads SET history_mode='full'")
    with pytest.raises(ValueError, match="profile"):
        native_databases.validate_state(value, VERSION)

    value = state_database()
    value.execute("DELETE FROM threads")
    with pytest.raises(ValueError, match="no inventoried thread"):
        native_databases.validate_state(value, VERSION)


def test_validate_quiescence_queue_and_goals():
    queue = database("CREATE TABLE queued_items(value TEXT)",
                     "CREATE TABLE queued_thread_revisions(thread_id TEXT)")
    native_databases.validate_quiescence(queue, "queue_1.sqlite", {THREAD}, VERSION)
    queue.execute("INSERT INTO queued_thread_revisions VALUES ('unknown')")
    with pytest.raises(ValueError, match="unknown thread"):
        native_databases.validate_quiescence(queue, "queue_1.sqlite", {THREAD}, VERSION)

    goals = database(
        "CREATE TABLE thread_goal_continuation_deferrals(value TEXT)",
        "CREATE TABLE thread_goals(thread_id TEXT, status TEXT)")
    native_databases.validate_quiescence(goals, "goals_1.sqlite", {THREAD}, VERSION)
    goals.execute("INSERT INTO thread_goals VALUES ('unknown','complete')")
    with pytest.raises(ValueError, match="unresolved"):
        native_databases.validate_quiescence(goals, "goals_1.sqlite", {THREAD}, VERSION)


def test_validate_quiescence_memories_requires_expected_consolidation():
    value = database(
        "CREATE TABLE jobs(value TEXT)", "CREATE TABLE stage1_outputs(value TEXT)",
        "CREATE TABLE consolidation_progress(singleton INTEGER, max_thread_count INTEGER)",
        "INSERT INTO consolidation_progress VALUES (1,0)")
    native_databases.validate_quiescence(value, "memories_1.sqlite", set(), VERSION)
    value.execute("UPDATE consolidation_progress SET max_thread_count=1")
    with pytest.raises(ValueError, match="consolidation"):
        native_databases.validate_quiescence(value, "memories_1.sqlite", set(), VERSION)
    native_databases.validate_quiescence(
        value, "memories_1.sqlite", set(), "0.154.0")


def projection_database():
    value = database(
        "CREATE TABLE thread_realtime_items(value TEXT)",
        "CREATE TABLE thread_turns(thread_id TEXT, turn_id TEXT, status TEXT, "
        "rollout_ordinal INTEGER, rollout_byte_offset INTEGER, "
        "rollout_end_ordinal INTEGER, rollout_end_byte_offset INTEGER)",
        "CREATE TABLE thread_items(thread_id TEXT, turn_id TEXT, item_id TEXT, "
        "item_type TEXT, rollout_ordinal INTEGER, updated_at_ordinal INTEGER, item_json TEXT)",
        "CREATE TABLE thread_history_projection_state(thread_id TEXT, "
        "next_rollout_byte_offset INTEGER, next_rollout_ordinal INTEGER)",
    )
    value.execute("INSERT INTO thread_turns VALUES (?,?,?,?,?,?,?)",
                  (THREAD, "turn", "completed", 1, 10, 4, 20))
    value.execute("INSERT INTO thread_items VALUES (?,?,?,?,?,?,?)",
                  (THREAD, "turn", "item", "agentMessage", 3, 3,
                   json.dumps({"id": "item", "type": "AgentMessage"})))
    value.execute("INSERT INTO thread_history_projection_state VALUES (?,?,?)",
                  (THREAD, 30, 5))
    return value


def histories():
    return {THREAD: {
        "turns": {"turn": {"start": (1, 10), "end": (4, 20)}},
        "items": {("turn", "item"): ("agentMessage", 3)},
        "size": 30, "next_ordinal": 5,
    }}


def test_validate_projection_accepts_exact_history_and_items():
    native_databases.validate_projection(projection_database(), histories())


@pytest.mark.parametrize("damage,message", [
    ("turn", "turn projection"),
    ("item", "item projection"),
    ("state", "projection is incomplete"),
    ("missing", "missing or extra"),
])
def test_validate_projection_rejects_mismatch(damage, message):
    value = projection_database()
    expected = histories()
    if damage == "turn":
        value.execute("UPDATE thread_turns SET rollout_byte_offset=11")
    elif damage == "item":
        value.execute("UPDATE thread_items SET item_type='wrong'")
    elif damage == "state":
        value.execute("UPDATE thread_history_projection_state SET next_rollout_ordinal=9")
    else:
        expected[THREAD]["turns"]["extra"] = {"start": (0, 0), "end": (0, 0)}
    with pytest.raises(ValueError, match=message):
        native_databases.validate_projection(value, expected)
