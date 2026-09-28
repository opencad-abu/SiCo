"""Finite ownership rules for legacy native homes and their private boundary."""

import re

from .native_schema import DATABASES
from .native_assets import DIRECTORIES, FILES

HOME = r"ai/agent/sessions/[a-zA-Z0-9_-]{1,96}/codex"
UUID = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
ROLLOUT = (r"sessions/[0-9]{4}/[0-9]{2}/[0-9]{2}/rollout-"
           r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}-" + UUID + r"\.jsonl")


def inside_home(relative):
    return re.fullmatch(HOME + "/.+", relative) is not None


def disposition(relative, directory):
    if directory and re.fullmatch(HOME, relative):
        return "container", "native_home"
    match = re.fullmatch("(" + HOME + ")/(.+)", relative)
    if match is None:
        return None
    path = match[2]
    if directory:
        if (path == "skills/.system" or path.startswith("skills/.system/")
                and path.removeprefix("skills/.system/") in DIRECTORIES):
            return "container", "native_system_skills"
        if (path in {"skills", "thread-writer-locks", "tmp", "tmp/arg0", ".tmp"}
                or re.fullmatch(r"sessions(?:/[0-9]{4}(?:/[0-9]{2}){0,2})?", path)
                or re.fullmatch(r"\.tmp/git-[a-zA-Z0-9]{6}(?:/(?:objects|refs))?", path)):
            return "container", "native_directory"
        return "review", "native_directory_adapter_required"
    for name in DATABASES:
        if path in {name, name + "-wal"}:
            return "archive", "native_sqlite" if path == name else "native_wal"
        if path == name + "-shm":
            return "rebuild", "native_sqlite_index"
    if re.fullmatch(ROLLOUT, path):
        return "archive", "native_rollout"
    if path.startswith("skills/.system/") and path.removeprefix("skills/.system/") in FILES:
        return "rebuild", "native_system_asset"
    if path == "session_index.jsonl":
        return "archive", "native_session_index"
    if path in {"config.toml", "installation_id", ".sandbox_migration"}:
        return "archive", "native_configuration"
    if (path in {"thread-writer-locks/.coordination.lock", ".tmp/plugins.sync.lock"}
            or re.fullmatch("thread-writer-locks/" + UUID + r"\.lock", path)):
        return "lock", "native_lock"
    if re.fullmatch(r"\.tmp/git-[a-zA-Z0-9]{6}/HEAD", path):
        return "archive", "native_git_head"
    return "review", "native_file_adapter_required"
