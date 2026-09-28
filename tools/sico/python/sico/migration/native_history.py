"""Admit qualified native histories to backup only; never restore an execution home."""

import os
import re
import sqlite3
from pathlib import PurePosixPath

from ..storage.native_origin import NativeOrigins
from .filesystem import fingerprint, issue
from .native_assets import FILES
from .native_databases import validate_projection, validate_quiescence, validate_state
from .native_paths import UUID
from .native_rollout import validate_index, validate_rollout
from .native_schema import DATABASES, home_version, validate_profile
from .sqlite_snapshot import snapshot


def validate_assets(entries):
    prefix = "skills/.system/"
    assets = {path[len(prefix):]: entry for path, entry in entries.items()
              if path.startswith(prefix) and entry["kind"] == "file"}
    if assets and ({path: row.get("sha256") for path, row in assets.items()} != FILES):
        raise ValueError("Native builtin assets are incomplete or modified")
    for path, entry in entries.items():
        if entry["schema"] == "native_system_asset" and entry.get("sha256") != FILES[path[len(prefix):]]:
            raise ValueError("Modified native builtin asset requires review")


def validate_origins(tree, root, inventory, histories):
    from .records import journal
    from .sessions import checked_stream

    session = PurePosixPath(root).parent.name
    entry = inventory.get(str(PurePosixPath(root).parent / "events.jsonl"))
    if entry is None:
        raise ValueError("Native home has no owning SiCo journal")
    origins, threads = NativeOrigins(), set()
    with checked_stream(tree, entry) as stream:
        for event in journal(stream, session, inventory):
            origins.observe(event)
            if event["kind"] == "codex.thread":
                threads.add(event["payload"]["thread_id"])
            if event["kind"] == "codex.child":
                raise ValueError("Native child history needs independent reconciliation")
    if threads != set(histories) or len(threads) != 1:
        raise ValueError("Native thread set differs from owning SiCo journal")
    expected = {(tid, turn) for tid, history in histories.items() for turn in history["turns"]}
    if set(origins.turns) != expected or any(task is None for task in origins.turns.values()):
        raise ValueError("Native turns differ from durable SiCo task bindings")


def validate_home(tree, root, inventory):
    prefix = root + "/"
    entries = {path[len(prefix):]: entry for path, entry in inventory.items() if path.startswith(prefix)}
    required = {*DATABASES, "thread-writer-locks/.coordination.lock"}
    if not required <= set(entries):
        raise ValueError("Native database or coordination lock is missing")
    validate_assets(entries)
    for path, entry in entries.items():
        if (entry["action"] in {"archive", "lock"} or entry["schema"] == "native_system_asset"):
            if entry["action"] == "lock":
                if entry["path"] not in tree.locks:
                    raise ValueError("Native writer lock is not held")
            elif "sha256" not in entry:
                raise ValueError("Native file has not been inventoried")
        if entry["schema"] == "native_git_head":
            with tree.open(entry["path"]) as stream:
                if (fingerprint(os.fstat(stream.fileno())) != entry["identity"]
                        or stream.read(64) != b"ref: refs/heads/main\n"):
                    raise ValueError("Native scratch Git metadata requires review")
    state = entries["state_5.sqlite"]
    with snapshot(tree, state, entries.get("state_5.sqlite-wal")) as connection:
        version = home_version(connection)
        validate_profile(connection, "state_5.sqlite", version)
        threads = validate_state(connection, version)
    histories, rollouts = {}, set()
    for identity, thread in threads.items():
        if re.fullmatch(UUID, identity) is None:
            raise ValueError("Invalid native thread identity")
        absolute = PurePosixPath(thread["path"])
        try:
            relative = str(absolute.relative_to(str(tree.path / root)))
        except ValueError:
            raise ValueError("Native rollout points outside its inventoried home") from None
        entry = entries.get(relative)
        if (entry is None or entry["schema"] != "native_rollout"
                or not relative.endswith("-" + identity + ".jsonl")):
            raise ValueError("Native rollout is missing or belongs to another thread")
        histories[identity] = validate_rollout(tree, entry, identity, thread["cwd"], version)
        rollouts.add(relative)
    if rollouts != {path for path, row in entries.items() if row["schema"] == "native_rollout"}:
        raise ValueError("Native home contains orphan rollouts")
    for name in DATABASES:
        if name == "state_5.sqlite":
            continue
        with snapshot(tree, entries[name], entries.get(name + "-wal")) as connection:
            validate_profile(connection, name, version)
            if name == "thread_history_1.sqlite":
                validate_projection(connection, histories)
            else:
                validate_quiescence(connection, name, set(threads), version)
    index = entries.get("session_index.jsonl")
    if index:
        validate_index(tree, index, set(threads))
    validate_origins(tree, root, inventory, histories)
    for path, entry in entries.items():
        if entry["schema"] == "native_lock" and PurePosixPath(path).name != ".coordination.lock":
            if path.startswith("thread-writer-locks/") and PurePosixPath(path).stem not in threads:
                raise ValueError("Native lock belongs to an unknown thread")


def validate_native_history(tree, rows):
    inventory, blockers = {row["path"]: row for row in rows}, []
    for entry in rows:
        if entry["schema"] != "native_home":
            continue
        try:
            validate_home(tree, entry["path"], inventory)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, sqlite3.Error):
            blockers.append(issue(entry["path"], "invalid_or_unresolved_native_history"))
    return blockers
