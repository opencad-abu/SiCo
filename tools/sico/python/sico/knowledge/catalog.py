"""Private per-definition storage paths and atomic SQLite generations."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import sqlite3
import stat

from sicolock import lock as state_lock
from ..storage.journal import open_private, private_dir
from ..storage.roots import state_root
from .contracts import KnowledgeError, digest


def source_key(source):
    return digest({k: v for k, v in source.items() if k not in {"name", "enabled", "source"}})


def directory(project, source, *, create=False):
    state = state_root(Path(project), create=create)
    path = state / "ai" / "knowledge" / source_key(source)
    for item in (state / "ai", state / "ai" / "knowledge", path):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise KnowledgeError("unsafe_cache", "知识索引目录必须是当前用户的私有目录")
    if create:
        if state.name != ".sico":
            raise KnowledgeError("migration_required", "请先完成工程状态迁移再建立知识索引")
        for item in (state / "ai", state / "ai" / "knowledge", path):
            private_dir(item)
    return path


def connection(path, *, readonly=False):
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
                or info.st_nlink != 1):
            raise KnowledgeError("unsafe_cache", "知识索引必须是当前用户的私有普通文件")
    elif readonly:
        raise KnowledgeError("pending_index", "请先更新此本地知识源的索引")
    else:
        os.close(open_private(path, os.O_CREAT | os.O_EXCL | os.O_RDWR))
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            fd = open_private(sidecar, os.O_RDONLY)
            try:
                if os.fstat(fd).st_nlink != 1:
                    raise KnowledgeError("unsafe_cache", "知识索引事务文件不能是硬链接")
            finally:
                os.close(fd)
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1, factory=Database) if readonly else (
        sqlite3.connect(str(path), timeout=1, factory=Database))
    con.row_factory = sqlite3.Row
    if not readonly:
        os.chmod(path, 0o600)
    return con


class Database(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


@contextmanager
def writer(path):
    fd = open_private(path / "build.lock", os.O_CREAT | os.O_RDWR)
    try:
        try:
            state_lock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise KnowledgeError("busy", "此知识源正在构建，请稍后重试") from None
        yield
    finally:
        os.close(fd)


def metadata(con):
    import json

    return json.loads(con.execute("SELECT value FROM metadata").fetchone()[0])
