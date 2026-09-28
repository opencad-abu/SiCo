"""Independent tombstones for explicitly discarded, unreadable local session remnants."""

import fcntl
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager

from sicolock import lock as state_lock

from ..core.contracts import identifier
from .history_reader import owned_directory
from .journal import open_private
from .project_files import read_record, write_record

DIRECTORY = "removed-records"
MESSAGE = "会话内容不完整，仅可删除残留记录，不能浏览或恢复"


def removed(root, session_id):
    identifier(session_id)
    directory = root / DIRECTORY
    try:
        owned_directory(directory)
        row = read_record(directory / (session_id + ".json"))
    except FileNotFoundError:
        return False
    if (not isinstance(row, dict) or set(row) != {
            "contract", "session_id", "version", "external_outcome"}
            or row["contract"] != "sico.record.removed.v1" or row["session_id"] != session_id
            or not isinstance(row["version"], str) or not re.fullmatch("[a-f0-9]{64}", row["version"])
            or row["external_outcome"] != "unconfirmed"):
        raise ValueError("Invalid removed-record receipt")
    return True


def assert_present(root, session_id):
    if removed(root, session_id):
        raise ValueError("此会话已删除")


def revision(root, session_id):
    """Bind confirmation to owned residual files without parsing damaged content."""
    identifier(session_id)
    owned_directory(root)
    owned_directory(root / "sessions")
    rows = []

    def visit(path, relative):
        try:
            info = path.lstat()
        except FileNotFoundError:
            rows.append((relative, "missing"))
            return
        if (info.st_uid != os.getuid() or relative == "session" and info.st_mode & 0o077
                or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                or stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
            raise ValueError("残留路径不是独占的本地会话文件，不能清理")
        identity = (relative, info.st_dev, info.st_ino, info.st_mode)
        if stat.S_ISDIR(info.st_mode):
            rows.append(identity)
            for child in sorted(path.iterdir()):
                if relative == "session" and child.name == "writer.lock":
                    continue
                visit(child, relative + "/" + child.name)
        else:
            rows.append((*identity, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        if len(rows) > 100000:
            raise ValueError("残留文件超过清理核对上限")

    visit(root / "sessions" / session_id, "session")
    attachments = root / "attachments"
    if attachments.exists():
        owned_directory(attachments)
    visit(attachments / session_id, "attachments")
    return hashlib.sha256(json.dumps(rows, ensure_ascii=True).encode("ascii")).hexdigest()


@contextmanager
def writer_lock(root, session_id):
    """Acquire the original writer lock without opening or repairing its journal."""
    directory = root / "sessions" / identifier(session_id)
    try:
        owned_directory(directory)
    except FileNotFoundError:
        yield
        return
    fd = open_private(directory / "writer.lock", os.O_CREAT | os.O_RDWR)
    try:
        if os.fstat(fd).st_nlink != 1:
            raise ValueError("Invalid residual writer lock")
        try:
            state_lock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("会话仍有写入进程，不能删除残留记录") from None
        yield
    finally:
        os.close(fd)


def discard(root, session_id, reviewed):
    if revision(root, session_id) != reviewed:
        raise ValueError("记录已变化，请刷新后重新核对")
    write_record(root / DIRECTORY / (session_id + ".json"), dict(
        contract="sico.record.removed.v1", session_id=session_id, version=reviewed,
        external_outcome="unconfirmed"))
