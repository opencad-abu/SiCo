"""Real owned processes, native command receipts and passive output inspection."""

import os
import subprocess
import sys
import threading
import time
from copy import deepcopy

import pytest

from cadai import process_monitor as monitor
from sico.service.workbench_data import WorkbenchService
from sico.storage.history import SessionReader
from sico.storage.journal import SessionJournal
from sico.storage.workbench import WorkbenchIndex
from sico.core.session_events import EventCursor
from test_codex_native_a2 import batch
from test_codex_native_projection import CONTEXT, begin, command, emit


@pytest.fixture
def service():
    value = WorkbenchService()
    try:
        yield value
    finally:
        value.close()
        assert value.wait(5)


def wait_for(condition):
    end = time.monotonic() + 5
    while not condition():
        assert time.monotonic() < end
        time.sleep(.01)


def test_owned_process_lifecycle_log_tail_and_session_isolation(tmp_path, service, monkeypatch):
    with SessionJournal(tmp_path, "processes") as journal:
        path = tmp_path / "worker.log"
        code = "import sys; print('ready <literal>',flush=True); sys.stdin.readline(); print('done',flush=True)"
        argv = [sys.executable, "-u", "-c", code]
        with path.open("wb") as log:
            process = subprocess.Popen(argv, cwd=tmp_path, stdin=subprocess.PIPE, stdout=log,
                                       stderr=log, text=True)
        with monitor.process_scope(str(journal.directory)):
            key = monitor.track(process, argv, tmp_path, name="dbAccess test", logs=(path,))
        try:
            wait_for(lambda: path.stat().st_size)
            source = service.source(journal)
            row = source.processes().result(5)["rows"][0]
            assert row["id"] == key and row["pid"] == process.pid
            assert row["state_label"] == "运行中"
            assert not monitor.snapshot(str(journal.directory) + "other")
            owner = threading.get_ident()
            read = monitor._log_tail

            def checked_read(paths):
                assert threading.get_ident() != owner
                return read(paths)

            monkeypatch.setattr(monitor, "_log_tail", checked_read)
            detail = source.detail("process", key).result(5)
            assert "ready <literal>" in detail["output"]
            process.communicate("finish\n", timeout=5)
            detail = source.detail("process", key).result(5)
            assert detail["process_state"] == "exited" and "done" in detail["output"]
            monkeypatch.setattr(monitor, "_log_tail", read)
            monitor.finish(key, process.returncode)
            with path.open("w") as log:
                log.write("early\n" + "x" * 100000 + "\nlatest marker")
            detail = source.detail("process", key).result(5)
            assert "latest marker" in detail["output"] and "early" not in detail["output"]
            assert detail["output_truncated"] is True and "最近的输出" in detail["prefix"]
            assert len(detail["output"]) < 75000
            with pytest.raises(ValueError):
                source.detail("process", "unowned").result(5)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


def test_pid_reuse_and_missing_permissions_do_not_claim_running(tmp_path, monkeypatch):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    key = monitor.track(process, process.args, tmp_path, session_id=str(tmp_path))
    try:
        actual = monitor.identity

        def reused(pid):
            value = actual(pid)
            value["start"] = "replacement"
            return value

        monkeypatch.setattr(monitor, "identity", reused)
        row = monitor.snapshot(str(tmp_path))[0]
        assert row["state"] == "exited" and row["pid"] == process.pid

        def forbidden(_pid):
            raise PermissionError()

        monkeypatch.setattr(monitor, "identity", forbidden)
        assert monitor.snapshot(str(tmp_path))[0]["state"] == "unknown"
        assert process.poll() is None
    finally:
        process.kill()
        process.wait()
        monitor.finish(key, process.returncode)


def test_shell_output_status_history_and_pty_id_are_not_a_system_pid(tmp_path, service):
    with SessionJournal(tmp_path, "shell") as journal:
        backend = begin(journal)
        emit(backend, "item/started", item=command(status="inProgress", exitCode=None,
                                                  processId=str(os.getpid())))
        source = service.source(journal)
        row = source.processes().result(5)["rows"][0]
        assert row["pid"] is None and row["state_label"] == "运行中"
        key = row["id"]
        emit(backend, "item/commandExecution/outputDelta", itemId="command", delta="first <raw>\n")
        backend.native.flush(force=True)
        detail = source.detail("process", key).result(5)
        assert "first <raw>" in detail["output"] and "命令会话编号" in detail["prefix"]
        emit(backend, "item/completed", item=command(exitCode=7, aggregatedOutput="final error"))
        assert source.processes().result(5)["rows"][0]["state_label"] == "失败"
        detail = source.detail("process", key).result(5)
        assert "final error" in detail["output"] and "first <raw>" not in detail["output"]
        backend._finish("failed")
        before = journal.events()
        preview = service.source(SessionReader(journal.root, journal.session_id), before)
        assert preview.processes().result(5)["rows"][0]["id"] == key
        assert preview.detail("process", key).result(5)["process_state"] == "failed"
        assert journal.events() == before


def test_missing_completion_stays_unknown_and_corrupt_output_is_visible(tmp_path, service):
    with SessionJournal(tmp_path, "uncertain") as journal:
        backend = begin(journal)
        emit(backend, "item/started", item=command(status="inProgress", exitCode=None))
        emit(backend, "item/commandExecution/outputDelta", itemId="command", delta="partial")
        backend.native.flush(force=True)
        source = service.source(journal)
        key = source.processes().result(5)["rows"][0]["id"]
        preview = service.source(SessionReader(journal.root, journal.session_id), journal.events())
        assert preview.processes().result(5)["rows"][0]["state_label"] == "待核对"
        backend._finish("cancelled")
        assert source.processes().result(5)["rows"][0]["state_label"] == "待核对"
        artifact = next(e["payload"]["artifact"] for e in journal.events()
                        if e["kind"] == "codex.item.output")
        (journal.root / artifact["path"]).write_text("tampered")
        assert "校验失败" in source.detail("process", key).result(5)["notice"]


@pytest.mark.parametrize("after_turn", [False, True])
def test_descendants_are_owned_and_exact_shell_match_has_real_pid(tmp_path, service, after_turn,
                                                                 monkeypatch):
    with SessionJournal(tmp_path, "tree") as journal:
        shell = "read token; printf done"
        code = ("import subprocess,sys; "
                "p=subprocess.Popen(['/bin/bash','-c',sys.argv[1]],stdin=subprocess.PIPE); "
                "print(p.pid,flush=True); sys.stdin.readline(); p.communicate(b'done\\n')")
        root = subprocess.Popen([sys.executable, "-u", "-c", code, shell], cwd=tmp_path,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        key = monitor.track(root, root.args, tmp_path, session_id=str(journal.directory), hidden=True)
        try:
            pid = int(root.stdout.readline())
            wait_for(lambda: any(r["pid"] == pid for r in monitor.snapshot(str(journal.directory))))
            backend = begin(journal)
            emit(backend, "item/started", item=command(command=shell, cwd=str(tmp_path),
                                                       status="inProgress", exitCode=None))
            if after_turn:
                backend._finish("completed")
            source = service.source(journal)
            row = source.processes().result(5)["rows"][0]
            assert row["pid"] == pid and row["id"].startswith("command_")
            assert source.processes().result(5)["total"] == 1
            with monkeypatch.context() as patch:
                def forbidden(_pid):
                    raise PermissionError()

                patch.setattr(monitor, "identity", forbidden)
                assert source.processes().result(5)["rows"][0]["state_label"] == "待核对"
            assert source.processes().result(5)["rows"][0]["state_label"] == "运行中"
            root.communicate("finish\n", timeout=5)
            monitor.finish(key, root.returncode)
            item = command(command=shell, cwd=str(tmp_path), aggregatedOutput="done")
            if after_turn:
                assert backend._notification({"method": "item/completed", "params": {
                    "threadId": "thread", "turnId": "turn", "item": item,
                }})
            else:
                emit(backend, "item/completed", item=item)
            row = source.processes().result(5)["rows"][0]
            assert row["pid"] == pid and row["state_label"] == "已完成"
        finally:
            if root.poll() is None:
                root.communicate("finish\n", timeout=5)
            monitor.finish(key, root.returncode)


def test_process_pages_bound_records_and_keep_old_commands_searchable(tmp_path, service):
    with SessionJournal(tmp_path, "pages") as journal:
        backend = begin(journal)
        for number in range(205):
            emit(backend, "item/completed", item=command(id="cmd" + str(number),
                                                       command="printf marker" + str(number)))
        source = service.source(journal)
        assert len(source.processes().result(5)["rows"]) == 200
        assert len(source.processes(offset=200).result(5)["rows"]) == 5
        assert source.processes(keyword="marker0").result(5)["total"] == 1


@pytest.mark.parametrize("new_task", [False, True])
def test_late_output_keeps_original_task_and_updates_sealed_process(tmp_path, service, new_task):
    with SessionJournal(tmp_path, "late-output") as journal:
        backend = begin(journal)
        emit(backend, "item/started", item=command(status="inProgress", exitCode=None))
        emit(backend, "item/commandExecution/outputDelta", itemId="command", delta="early|")
        backend._finish("completed")
        original_task = backend.state.task["id"]
        backend.native_history.capture_turn({"id": "turn", "status": "completed", "items": [
            command(status="inProgress", exitCode=None),
        ]})
        if new_task:
            assert backend.state.task["status"] == "needs_reconcile"
            backend.acknowledge_interrupted()
            backend._begin("New task", CONTEXT, "next-input", "chat")
            backend.native_history.bind("next-turn")
        state = deepcopy(backend.state.record())
        event = {"method": "item/commandExecution/outputDelta", "params": {
            "threadId": "thread", "turnId": "turn", "itemId": "command", "delta": "late<raw>|",
        }}
        notify = (lambda value: backend.native_history.notification(value, "next-turn")
                  if new_task else backend._notification(value))
        for fields in ({"threadId": "foreign"}, {"turnId": "unknown"}, {"itemId": "unknown"}):
            assert not notify({**event, "params": {**event["params"], **fields}})
        assert notify(event)
        assert notify(event)  # Identical text is two real chunks, not a duplicate receipt.
        assert backend.state.record() == state
        source = service.source(journal)
        row = source.processes().result(5)["rows"][0]
        detail = source.detail("process", row["id"]).result(5)
        assert "early|late<raw>|late<raw>|" in detail["output"]
        assert original_task in detail["prefix"] and detail["process_state"] == "unknown"
        if new_task:
            assert backend.state.task["id"] not in detail["output"]
        index = WorkbenchIndex(journal).sync()
        late_rows = [r for r in index.data.values() if r["data_type"] == "archive" and "origin" in r]
        assert len(late_rows) == 2 and all(r["task_id"] == original_task for r in late_rows)
        assert all(index.evidence_value(r["id"])["text"] == "late<raw>|" for r in late_rows)
        cursor = EventCursor(journal.session_id, "runtime", CONTEXT)
        cursor.consume(batch(journal))
        forged = deepcopy(batch(journal))
        forged["events"][-1]["payload"]["origin"]["task_id"] = "different-task"
        with pytest.raises(ValueError, match="origin"):
            EventCursor(journal.session_id, "runtime", CONTEXT).consume(forged)
        artifact = journal.events()[-1]["payload"]["artifact"]
        (journal.root / artifact["path"]).write_text("tampered")
        assert "校验失败" in source.detail("process", row["id"]).result(5)["notice"]
        assert notify({"method": "item/completed", "params": {
            "threadId": "thread", "turnId": "turn", "item": command(aggregatedOutput="complete"),
        }})
        detail = source.detail("process", row["id"]).result(5)
        assert detail["process_state"] == "completed" and "complete" in detail["output"]
        count = len(journal.events())
        assert notify(event) and len(journal.events()) == count
        assert backend.state.record() == state


def test_large_late_output_is_archived_in_bounded_chunks_and_displays_tail(tmp_path, service):
    with SessionJournal(tmp_path, "late-large") as journal:
        backend = begin(journal)
        emit(backend, "item/started", item=command(status="inProgress", exitCode=None))
        backend._finish("completed")
        text = "first-output|" + "日志" * 40000 + "|last-output"
        assert backend._notification({"method": "item/commandExecution/outputDelta", "params": {
            "threadId": "thread", "turnId": "turn", "itemId": "command", "delta": text,
        }})
        index = WorkbenchIndex(journal).sync()
        chunks = [index.evidence_value(row["id"])["text"] for row in index.data.values()
                  if row["data_type"] == "archive"]
        assert "".join(chunks) == text and all(len(chunk) <= 16384 for chunk in chunks)
        source = service.source(journal)
        key = source.processes().result(5)["rows"][0]["id"]
        detail = source.detail("process", key).result(5)
        assert "last-output" in detail["output"] and "first-output" not in detail["output"]
        assert detail["output_truncated"] is True and "最近的输出" in detail["prefix"]
        assert len(detail["output"]) < 70000
