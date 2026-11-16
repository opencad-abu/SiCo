"""Record metadata commands stay serialized, reviewed and project-scoped."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from sico.core.contracts import BoundContext
from sico.service import session_records
from sico.transport.framing import ProtocolError

PROJECT = "a" * 32
VERSION = "b" * 64


class Hub:
    def __init__(self, tmp_path):
        self.descriptor = SimpleNamespace(project_id=PROJECT)
        self.owner = SimpleNamespace(
            project=tmp_path, controllers={}, _mutation=nullcontext())
        self.retired = []
        self.recovery = SimpleNamespace(retired=self.retired.append)
        self.catalog_requests = []
        self.workspace = SimpleNamespace(catalog=SimpleNamespace(
            request=lambda: SimpleNamespace(result=lambda: self.catalog_requests.append(True))))


def facts(**overrides):
    value = dict(
        snapshot={"project_id": PROJECT, "provider_config": None,
                  "environment": {}, "bridge_identity": {}},
        deleted=False,
        unresolved=False,
        ends=(),
        version=VERSION,
        context=BoundContext("instance", "generation", "target", {}),
        inputs=(),
        task={},
    )
    value.update(overrides)
    return SimpleNamespace(**value)


def params(**overrides):
    value = dict(session_id="old", runtime_id=None, action="rename",
                 name="new name")
    value.update(overrides)
    return value


def wire():
    return SimpleNamespace(connection=SimpleNamespace(
        closed=SimpleNamespace(is_set=lambda: False)))


def install(monkeypatch, value=None, damaged=None):
    calls = []
    monkeypatch.setattr(session_records, "inspect_record",
                        lambda *_args: (value or facts(), damaged))
    monkeypatch.setattr(session_records, "_write_record",
                        lambda *args: calls.append(args))
    monkeypatch.setattr(session_records, "delete_remnant",
                        lambda *args: calls.append(args))
    return calls


def test_record_command_validation_rejects_malformed_or_unsafe_requests(
        tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    install(monkeypatch)
    for value in (
        {},
        {**params(), "extra": True},
        params(review_version="c" * 64),
        params(review_version="bad", action="delete"),
        params(action="remove"),
        params(runtime_id="bad/id"),
        params(name=None),
        params(action="delete", name="unexpected"),
        params(name=""),
        params(name="bad\0name"),
        params(name="x" * 300),
    ):
        with pytest.raises((ProtocolError, ValueError)):
            session_records.record_command(hub, wire(), value)


def test_record_command_rejects_active_foreign_and_stale_runtime(
        tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    calls = install(monkeypatch)
    hub.owner.controllers["old"] = object()
    with pytest.raises(ValueError, match="活动会话"):
        session_records.record_command(hub, wire(), params())
    hub.owner.controllers.clear()

    install(monkeypatch, facts(snapshot={"project_id": "c" * 32}))
    with pytest.raises(ProtocolError, match="another project"):
        session_records.record_command(hub, wire(), params())

    install(monkeypatch, facts(ends=(
        {"payload": {"address": {"runtime_id": "d" * 32}}},)))
    with pytest.raises(ValueError, match="运行实例"):
        session_records.record_command(hub, wire(), params(runtime_id="e" * 32))
    assert calls == []
    calls = install(monkeypatch, facts(ends=(
        {"payload": {"address": {"runtime_id": "d" * 32}}},)))
    session_records.record_command(hub, wire(), params(runtime_id="d" * 32))
    assert calls


def test_record_command_deletes_damaged_records_only(tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    calls = install(monkeypatch, damaged={"reason": "damaged"})
    with pytest.raises(ValueError, match="仅可删除"):
        session_records.record_command(hub, wire(), params())
    assert calls == []

    assert session_records.record_command(hub, wire(), params(action="delete",
                                                               name=None)) is None
    assert calls[0][2:] == ("old", None)
    assert hub.retired == ["old"]
    assert hub.catalog_requests == [True]


def test_record_command_deleted_rename_is_rejected_but_delete_is_idempotent(
        tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    calls = install(monkeypatch, facts(deleted=True))
    with pytest.raises(ValueError, match="已删除"):
        session_records.record_command(hub, wire(), params())
    assert calls == []
    session_records.record_command(hub, wire(), params(action="delete",
                                                        name=None))
    assert calls == []
    assert hub.retired == ["old"]


def test_record_command_requires_review_for_unresolved_work(tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    calls = install(monkeypatch, facts(unresolved=True))
    with pytest.raises(ValueError, match="未决工作"):
        session_records.record_command(hub, wire(), params())
    session_records.record_command(
        hub, wire(), params(action="delete", name=None,
                            review_version="c" * 64))
    assert calls[0][2]["session_id"] == "old"
    assert calls[0][4] == "c" * 64
    assert hub.retired == ["old"]


def test_record_command_writes_rename_and_retires_delete(tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    calls = install(monkeypatch)
    session_records.record_command(hub, wire(), params())
    assert calls[0][1] is not None
    assert hub.catalog_requests == [True]
    session_records.record_command(hub, wire(), params(action="delete",
                                                        name=None))
    assert len(calls) == 2 and hub.retired == ["old"]


class Journal:
    def __init__(self):
        self.session_id = "old"
        self.events_rows = []
        self.appended = []
        self.state = None

    def events(self, exclude=None):
        return self.events_rows

    def append(self, kind, payload, state):
        self.appended.append((kind, payload, state))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def install_write(monkeypatch, locked):
    journal = Journal()
    monkeypatch.setattr(session_records, "SessionJournal", lambda *_args: journal)
    monkeypatch.setattr(session_records, "read_facts", lambda *_args: locked)
    return journal


def test_write_record_rejects_changed_evidence_or_closed_connection(
        tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    locked = facts(version=VERSION)
    install_write(monkeypatch, locked)
    with pytest.raises(ValueError, match="记录已变化"):
        session_records._write_record(hub, wire(), params(),
                                      facts(version="c" * 64), None)

    with pytest.raises(ValueError, match="记录已变化"):
        session_records._write_record(
            hub, wire(), params(), facts(), "d" * 64)

    closed = SimpleNamespace(connection=SimpleNamespace(
        closed=SimpleNamespace(is_set=lambda: True)))
    with pytest.raises(ValueError, match="连接已断开"):
        session_records._write_record(hub, closed, params(), facts(), None)


def test_write_record_reviewed_delete_keeps_external_outcome_unknown(
        tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    locked = facts(unresolved=True, inputs=({"input_id": "one"},),
                   ends=({"payload": {"end": 1}},))
    journal = install_write(monkeypatch, locked)
    reviewed = []
    monkeypatch.setattr(session_records, "require_review",
                        lambda *args: reviewed.append(args))
    session_records._write_record(
        hub, wire(), params(action="delete", name=None,
                            review_version="c" * 64),
        facts(), VERSION)
    assert reviewed and journal.appended[0][0] == "codex.thread.deleted"
    payload = journal.appended[0][1]
    assert payload["abandoned_inputs"] == ["one"]
    assert payload["external_outcome"] == "unconfirmed"
    assert payload["original_end"] == {"end": 1}


def test_write_record_delegates_committed_metadata(tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    locked = facts()
    install_write(monkeypatch, locked)
    calls = []
    monkeypatch.setattr(session_records, "_mutate",
                        lambda *args: calls.append(args))
    session_records._write_record(hub, wire(), params(), facts(), None)
    assert calls and calls[0][1].session_id == "old"


def test_mutate_without_threads_writes_name_and_tombstone(tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    journal = Journal()
    session_records._mutate(hub.owner, journal, facts(), params())
    assert journal.appended[-1][0] == "codex.thread.name"
    assert journal.appended[-1][1]["source"] == "user"
    session_records._mutate(hub.owner, journal, facts(), params(action="delete",
                                                                name=None))
    assert journal.appended[-1][0] == "codex.thread.deleted"
    assert journal.appended[-1][1] == {}


def test_mutate_requires_thread_identity_and_snapshot(tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    journal = Journal()
    journal.events_rows = [{"kind": "codex.thread", "payload": {"thread_id": " "}}]
    with pytest.raises(ValueError, match="线程标识"):
        session_records._mutate(hub.owner, journal, facts(), params())
    journal.events_rows = [{"kind": "codex.thread", "payload": {"thread_id": "t"}}]
    with pytest.raises(ValueError, match="模型配置"):
        session_records._mutate(hub.owner, journal, facts(snapshot=None), params())


def test_mutate_uses_one_metadata_backend_and_closes_it(tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    journal = Journal()
    journal.events_rows = [{"kind": "codex.thread", "payload": {"thread_id": "t"}}]
    actions = []
    monkeypatch.setattr(session_records, "recovery_environment",
                        lambda *_args: {"ENV": "1"})
    monkeypatch.setattr(session_records, "provider_from_config",
                        lambda *_args, **_kwargs: "provider")
    class Backend:
        def delete_thread(self):
            actions.append("delete")
        def set_thread_name(self, name):
            actions.append(("rename", name))
        def close(self):
            actions.append("close")
    monkeypatch.setattr(session_records, "create_backend",
                        lambda *_args, **_kwargs: Backend())
    session_records._mutate(hub.owner, journal, facts(), params())
    assert actions == [("rename", "new name"), "close"]
    session_records._mutate(hub.owner, journal, facts(), params(action="delete",
                                                                name=None))
    assert actions[-2:] == ["delete", "close"]
