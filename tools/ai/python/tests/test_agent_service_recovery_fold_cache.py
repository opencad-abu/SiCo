"""Repeated recovery reads reuse the validated journal fold instead of rescanning."""

import json
import threading
import time

from agent_service_recovery_fixtures import seed
from sico.core.contracts import RunState
from sico.service import recovery_facts
from sico.service.recovery_facts import read_facts
from sico.storage.journal import SessionJournal
from sico.storage.roots import agent_root


def fold_path(project):
    return agent_root(project) / "sessions" / "old" / "events.fold.json"


def counting(monkeypatch):
    """Count full journal folds while keeping the real implementation."""

    calls = []
    original = recovery_facts.fold_journal

    def counted(session_id, events, saved, **kwargs):
        calls.append(session_id)
        return original(session_id, events, saved, **kwargs)

    monkeypatch.setattr(recovery_facts, "fold_journal", counted)
    return calls


def test_recovery_reads_reuse_the_validated_fold(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch, status="queued")
    first = read_facts(tmp_path, "old")
    assert fold_path(tmp_path).exists()
    assert fold_path(tmp_path).stat().st_mode & 0o077 == 0
    calls = counting(monkeypatch)
    assert read_facts(tmp_path, "old") == first
    assert calls == []          # 指纹未变：缓存命中，不再整篇折叠


def test_operation_scoped_reads_reuse_the_same_fold(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch, status="queued")
    first = read_facts(tmp_path, "old")
    calls = counting(monkeypatch)
    scoped = read_facts(tmp_path, "old", "missing-operation")
    assert calls == [] and scoped == first      # 操作过滤发生在折叠之后


def test_end_scoped_reads_still_fold_fresh(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch, status="queued")
    read_facts(tmp_path, "old")
    calls = counting(monkeypatch)
    read_facts(tmp_path, "old", end_operation=("runtime", "operation"))
    assert calls == ["old"]     # 结束核对按 operation 过滤，不共享无过滤的缓存


def test_journal_change_invalidates_the_fold_once(tmp_path, monkeypatch):
    context, _address = seed(tmp_path, monkeypatch, status="queued")
    first = read_facts(tmp_path, "old")
    calls = counting(monkeypatch)
    with SessionJournal(tmp_path, "old") as journal:
        journal.append("session.note", {"text": "after the fold"},
                       journal.state or RunState(context))
    second = read_facts(tmp_path, "old")
    assert calls == ["old"] and second.version != first.version
    assert read_facts(tmp_path, "old") == second
    assert calls == ["old"]     # 新指纹同样写回缓存


def test_invalid_fold_cache_falls_back_and_is_replaced(tmp_path, monkeypatch):
    seed(tmp_path, monkeypatch, status="queued")
    first = read_facts(tmp_path, "old")
    fold_path(tmp_path).write_text('{"contract": "other"}')
    calls = counting(monkeypatch)
    assert read_facts(tmp_path, "old") == first
    assert calls == ["old"]     # 缓存不可用：回退完整折叠并重写缓存
    assert json.loads(fold_path(tmp_path).read_text())["contract"] == recovery_facts.FOLD_CONTRACT


def test_concurrent_readers_share_one_fold(tmp_path, monkeypatch):
    """扫描与用户操作同时到达时，整篇折叠只发生一次。"""
    seed(tmp_path, monkeypatch, status="queued")
    calls = []
    entered, release = threading.Event(), threading.Event()
    original = recovery_facts.fold_journal

    def slow(session_id, events, saved, **kwargs):
        calls.append(session_id)
        entered.set()
        assert release.wait(5)
        return original(session_id, events, saved, **kwargs)

    monkeypatch.setattr(recovery_facts, "fold_journal", slow)
    results, errors = [], []

    def read():
        try:
            results.append(read_facts(tmp_path, "old"))
        except Exception as exc:            # noqa: BLE001 - the assertion below reports it
            errors.append(exc)

    readers = [threading.Thread(target=read) for _ in range(3)]
    for reader in readers:
        reader.start()
    assert entered.wait(5)
    time.sleep(0.3)             # 其余读者到达折叠决策点；单飞时它们等在锁上
    release.set()
    for reader in readers:
        reader.join(10)
    assert not errors and calls == ["old"] and len(results) == 3
    assert all(facts == results[0] for facts in results)


def counting_rows(monkeypatch):
    """Count folded rows per call while keeping the real implementation."""

    calls, rows = [], []
    original = recovery_facts.fold_journal

    def counted(session_id, events, saved, **kwargs):
        calls.append(session_id)
        materialized = list(events)
        rows.append(len(materialized))
        return original(session_id, iter(materialized), saved, **kwargs)

    monkeypatch.setattr(recovery_facts, "fold_journal", counted)
    return calls, rows


def test_appended_rows_extend_the_cached_fold(tmp_path, monkeypatch):
    """追加写入后只折新增行，且与全量折叠得到同一版本。"""

    context, _address = seed(tmp_path, monkeypatch, status="tool")
    first = read_facts(tmp_path, "old")
    with SessionJournal(tmp_path, "old") as journal:
        state = journal.state or RunState(context)
        journal.append("session.note", {"text": "after the fold"}, state)
        journal.append("session.note", {"text": "and again"}, state)
    calls, rows = counting_rows(monkeypatch)
    extended = read_facts(tmp_path, "old")
    assert calls == ["old"] and rows == [2]        # 只折新增的两行
    assert extended.version != first.version
    fold_path(tmp_path).unlink()                    # 全量重折：版本必须一致
    fresh = read_facts(tmp_path, "old")
    assert fresh == extended and fresh.version == extended.version


def test_broken_fold_coverage_falls_back_to_the_full_fold(tmp_path, monkeypatch):
    """覆盖位置损坏时回退整篇折叠，并重写一份可用缓存。"""

    seed(tmp_path, monkeypatch, status="tool")
    first = read_facts(tmp_path, "old")
    row = json.loads(fold_path(tmp_path).read_text())
    row["covered"] = row["covered"] - 5            # 指向行中间：扩展必然读错
    fold_path(tmp_path).write_text(json.dumps(row))
    with SessionJournal(tmp_path, "old") as journal:
        journal.append("session.note", {"text": "later"}, journal.state)
    total = sum(1 for _ in open(fold_path(tmp_path).parent / "events.jsonl", "rb"))
    calls, rows = counting_rows(monkeypatch)
    second = read_facts(tmp_path, "old")
    # 第一次是扩展尝试（读坏即失败，未折任何行），第二次回退整篇。
    assert calls == ["old", "old"] and rows == [total]
    assert second.version != first.version
    assert json.loads(fold_path(tmp_path).read_text())["covered"] > row["covered"]
