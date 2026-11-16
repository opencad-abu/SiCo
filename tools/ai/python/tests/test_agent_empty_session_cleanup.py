"""Project service shutdown discards sessions that never received user input."""

from types import SimpleNamespace

import pytest
from sico.core.contracts import BoundContext, RunState
from sico.service.project_sessions import ProjectSessionOwner
from sico.storage.empty_session import discard_empty_session, discard_empty_sessions
from sico.storage.history import CatalogIndex
from sico.storage.journal import SessionJournal
from sico.storage.roots import agent_root


class Bridge:
    def __init__(self, _descriptor):
        self.closed = False

    def register_session(self, _session_id, _context):
        pass

    def close(self):
        self.closed = True


def context(target):
    return BoundContext("instance", "generation", target, {"cwd": "/project"})


def task_started(controller, input_id, origin):
    controller.journal.append("task.started", {
        "text": "system context" if origin == "startup" else "user request",
        "context": controller.current.record(),
        "input_id": input_id,
        "origin": origin,
    }, RunState(controller.current))


def queued_input(controller, input_id, origin="chat"):
    message = {
        "kind": "submit",
        "id": input_id,
        "text": "queued user request",
        "context": controller.current.record(),
    }
    controller.inbox.enqueue(message, controller.current, origin)
    controller.history.record(controller.inbox.pending[-1])


def test_service_close_discards_empty_and_system_only_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sico.service.project_sessions.provider_from_config",
        lambda config, environment: SimpleNamespace(
            label=config["model"], base="test", model=config["model"]
        ),
    )
    ids = {
        "empty": "a" * 32,
        "startup": "b" * 32,
        "thread_operation": "c" * 32,
        "user_task": "d" * 32,
        "user_queue": "e" * 32,
        "startup_queue": "f" * 32,
    }
    owner = ProjectSessionOwner(
        tmp_path, provider_config={"model": "test"}, environment={}, bridge_factory=Bridge
    )
    try:
        controllers = {
            name: owner.open(session_id, {"endpoint": name}, context(name))
            for name, session_id in ids.items()
        }
        task_started(controllers["startup"], "1" * 32, "startup")
        task_started(controllers["thread_operation"], "2" * 32, "thread_operation")
        task_started(controllers["user_task"], "3" * 32, "chat")
        queued_input(controllers["user_queue"], "4" * 32)
        queued_input(controllers["startup_queue"], "5" * 32, "startup")
        owner.retire_session(controllers.pop("empty"))
    finally:
        owner.close()

    root = agent_root(tmp_path)
    for name in ("empty", "startup", "thread_operation", "startup_queue"):
        assert not (root / "sessions" / ids[name]).exists()
        assert not (root / "attachments" / ids[name]).exists()
    for name in ("user_task", "user_queue"):
        assert (root / "sessions" / ids[name]).is_dir()
        assert (root / "attachments" / ids[name]).is_dir()
    assert set(CatalogIndex(root).scan()) == {ids["user_task"], ids["user_queue"]}


def test_an_active_writer_is_never_discarded(tmp_path):
    session_id = "f" * 32
    root = agent_root(tmp_path, create=True)
    journal = SessionJournal(tmp_path, session_id)
    try:
        with pytest.raises(ValueError, match="写入进程"):
            discard_empty_session(root, session_id)
        assert journal.directory.is_dir()
    finally:
        journal.close()
    assert discard_empty_session(root, session_id) is True
    assert not journal.directory.exists()


def test_unreadable_record_is_preserved_fail_closed(tmp_path):
    session_id = "0" * 32
    root = agent_root(tmp_path, create=True)
    with SessionJournal(tmp_path, session_id) as journal:
        events = journal.directory / "events.jsonl"
    events.write_text("not-json\n", encoding="utf-8")
    assert discard_empty_sessions(root) == ()
    assert journal.directory.is_dir()
