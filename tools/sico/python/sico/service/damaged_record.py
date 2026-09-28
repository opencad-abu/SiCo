"""Qualify and discard incomplete inactive records without constructing an execution backend."""

from ..storage.history import CatalogIndex
from ..storage.record_removal import MESSAGE, discard, removed, revision, writer_lock
from ..storage.roots import agent_root
from .recovery_facts import read_facts


def inspect_record(project, session_id):
    root = agent_root(project)
    if removed(root, session_id):
        return None, dict(version="0" * 64, removed=True)
    try:
        facts = read_facts(project, session_id)
    except (ValueError, FileNotFoundError, KeyError, TypeError):
        return None, dict(version=revision(root, session_id), removed=False)
    return facts, None


def require_readable(reader):
    """The preview fast path checks component presence without loading all state."""
    row = CatalogIndex(reader.root).read_session(reader.session_id)
    if row.get("unavailable"):
        raise ValueError("此会话已删除")


def delete_remnant(hub, wire, session_id, reviewed):
    root = agent_root(hub.owner.project)
    if removed(root, session_id):
        return
    if reviewed is None:
        raise ValueError(MESSAGE + "；请先确认删除")
    with writer_lock(root, session_id):
        _, observation = inspect_record(hub.owner.project, session_id)
        if observation is None or observation["version"] != reviewed:
            raise ValueError("记录已变化，请刷新后重新核对")
        if wire.connection.closed.is_set():
            raise ValueError("记录操作连接已断开")
        discard(root, session_id, reviewed)
