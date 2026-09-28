"""Cross-check native database ownership and terminal state against immutable rollouts."""

from ..transport.framing import strict_json
from .native_rollout import MAX_ROWS, item_status


def records(connection, query):
    cursor = connection.execute(query)
    for count, row in enumerate(cursor):
        if count >= MAX_ROWS:
            raise ValueError("Native database exceeds validation limit")
        yield row


def empty(connection, tables):
    for table in tables:
        if connection.execute('SELECT 1 FROM "' + table + '" LIMIT 1').fetchone():
            raise ValueError("Native state requires a dedicated reconciliation adapter")


def validate_state(connection, version):
    empty(connection, ("thread_attachments" if version == "0.156.1" else "thread_artifacts",))
    empty(connection, (
        "thread_dynamic_tools", "thread_spawn_edges", "remote_control_enrollments",
        "external_agent_config_imports", "rollout_migration_state",
        "rollout_migration_skipped_rollouts", "projects", "project_roots",
        "project_idempotency_keys",
    ))
    backfills = list(records(connection, "SELECT status FROM backfill_state"))
    if backfills != [("complete",)]:
        raise ValueError("Native state backfill is incomplete")
    threads = {}
    for identity, path, cwd, recorded_version, mode in records(connection,
            "SELECT id, rollout_path, cwd, cli_version, history_mode FROM threads"):
        if (recorded_version != version or mode != "paginated" or not isinstance(identity, str)
                or identity in threads or not isinstance(path, str) or not isinstance(cwd, str)):
            raise ValueError("Native thread profile is unqualified")
        threads[identity] = {"path": path, "cwd": cwd}
    if not threads:
        raise ValueError("Native home has no inventoried thread")
    return threads


def validate_quiescence(connection, name, thread_ids, version):
    if name == "queue_1.sqlite":
        empty(connection, ("queued_items",))
        for (identity,) in records(connection, "SELECT thread_id FROM queued_thread_revisions"):
            if identity not in thread_ids:
                raise ValueError("Native queue revision belongs to an unknown thread")
    elif name == "goals_1.sqlite":
        empty(connection, ("thread_goal_continuation_deferrals",))
        for identity, status in records(connection, "SELECT thread_id, status FROM thread_goals"):
            if identity not in thread_ids or status != "complete":
                raise ValueError("Native goal is unresolved")
    elif name == "memories_1.sqlite":
        # Job kinds, leases and retry policy cannot be inferred from an empty sample.
        empty(connection, ("jobs", "stage1_outputs"))
        if version == "0.156.1":
            progress = connection.execute(
                "SELECT singleton, max_thread_count FROM consolidation_progress").fetchall()
            if progress != [(1, 0)]:
                raise ValueError("Native memory consolidation requires review")


def validate_projection(connection, histories):
    empty(connection, ("thread_realtime_items",))
    seen_turns, seen_items, seen_projections = set(), set(), set()
    for tid, turn, status, start, offset, last, end in records(connection,
            "SELECT thread_id, turn_id, status, rollout_ordinal, rollout_byte_offset, "
            "rollout_end_ordinal, rollout_end_byte_offset FROM thread_turns"):
        history = histories.get(tid, {}).get("turns", {}).get(turn)
        if (history is None or status != "completed" or history["start"] != (start, offset)
                or history["end"] != (last, end)):
            raise ValueError("Native turn projection does not match its rollout")
        seen_turns.add((tid, turn))
    for tid, turn, identity, kind, ordinal, updated, raw in records(connection,
            "SELECT thread_id, turn_id, item_id, item_type, rollout_ordinal, "
            "updated_at_ordinal, item_json FROM thread_items"):
        item = strict_json(raw)
        history = histories.get(tid, {}).get("items", {}).get((turn, identity))
        if (not isinstance(item, dict) or item.get("id") != identity or item_status(item) != kind
                or history != (kind, updated) or ordinal != updated or (tid, turn) not in seen_turns):
            raise ValueError("Native item projection does not match its rollout")
        seen_items.add((tid, turn, identity))
    for tid, offset, ordinal in records(connection,
            "SELECT thread_id, next_rollout_byte_offset, next_rollout_ordinal "
            "FROM thread_history_projection_state"):
        history = histories.get(tid)
        if history is None or (offset, ordinal) != (history["size"], history["next_ordinal"]):
            raise ValueError("Native history projection is incomplete")
        seen_projections.add(tid)
    expected_turns = {(tid, turn) for tid, history in histories.items() for turn in history["turns"]}
    expected_items = {(tid, turn, item) for tid, history in histories.items()
                      for turn, item in history["items"]}
    if (seen_turns != expected_turns or seen_items != expected_items
            or seen_projections != set(histories)):
        raise ValueError("Native history projection has missing or extra identities")
