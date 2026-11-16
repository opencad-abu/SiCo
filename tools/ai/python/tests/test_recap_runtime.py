"""Real pinned app-server recap isolation through both Responses transports."""
# ruff: noqa: F401, F811 -- pytest fixture registration and injection

import json
import threading

import pytest
from test_agent_codex_native import CONTEXT, answer, gateway, native_codex, registry, settings  # noqa: F401

from sico.codex.backend import CodexBackend
from sico.service.controller import SessionController
from sico.storage.journal import SessionJournal


@pytest.mark.parametrize("tool_format", ["native", "flat"])
def test_recap_keeps_original_history_and_exposes_no_tools(tmp_path, gateway, native_codex, tool_format):
    provider = settings(gateway, native_codex, tool_format)
    with SessionJournal(tmp_path, "recap") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        owner = SessionController(backend)
        try:
            gateway.responses.put(answer("已完成检查，下一步确认接口。"))
            owner.submit("检查接口")
            assert owner.wait(20) and owner.execution_view().task["status"] == "completed"
            native_thread = backend.thread_id
            count = len(journal.records)
            gateway.responses.put(answer(json.dumps({"recap": "接口检查已完成，下一步确认接口。"})))
            snapshot = owner.recap.start(expected_task=owner.execution_view().task["id"], request_id="recap-1")
            assert snapshot["busy"]
            assert owner.recap.wait(40)
            result = owner.recap.status()
            assert result["text"] == "接口检查已完成，下一步确认接口。", result
            assert not result["error"]
            assert backend.thread_id == native_thread and len(journal.records) == count
            request = gateway.requests[-1]
            assert request.get("tools", []) == []
            assert request["model"] == provider.model
            assert "检查接口" in json.dumps(request, ensure_ascii=False)
            assert not list(journal.directory.glob("recap-*"))
            gateway.responses.put(answer("已确认接口。"))
            owner.submit("确认接口")
            assert owner.wait(20)
            assert owner.recap.status()["text"] == ""
            assert len(gateway.requests) == 3
        finally:
            owner.close()
            assert owner.wait(10)


def test_running_recap_is_cancelled_by_new_task_without_delaying_it(tmp_path, gateway, native_codex):
    entered, release = threading.Event(), threading.Event()
    with SessionJournal(tmp_path, "recap-cancel") as journal:
        backend = CodexBackend(settings(gateway, native_codex, "native"), registry([]), journal, CONTEXT)
        owner = SessionController(backend)
        try:
            gateway.responses.put(answer("First answer"))
            owner.submit("First task")
            assert owner.wait(20)
            def delayed(_request):
                entered.set()
                release.wait(15)
                return answer('{"recap":"Outdated answer"}')
            gateway.responses.put(delayed)
            owner.recap.start(expected_task=owner.execution_view().task["id"], request_id="delayed")
            assert entered.wait(15)
            gateway.responses.put(answer("New answer"))
            owner.submit("New task")
            assert owner.wait(10)
            assert owner.execution_view().task["status"] == "completed"
            assert owner.recap.status()["text"] == ""
        finally:
            release.set()
            owner.close()
            assert owner.wait(10)


def test_failed_automatic_recap_requires_manual_retry(tmp_path, gateway, native_codex):
    with SessionJournal(tmp_path, "recap-retry") as journal:
        backend = CodexBackend(settings(gateway, native_codex, "native"), registry([]), journal, CONTEXT)
        owner = SessionController(backend)
        try:
            for index in range(3):
                gateway.responses.put(answer("检查完成"))
                owner.submit(f"检查接口 {index}")
                assert owner.wait(20)
            task = owner.execution_view().task["id"]
            gateway.responses.put(answer("invalid structured output"))
            owner.recap.start(expected_task=task, request_id="first", automatic=True)
            assert owner.recap.wait(40)
            assert owner.recap.status()["error"]
            snapshot = owner.recap.start(expected_task=task, request_id="returned", automatic=True)
            assert not snapshot["busy"] and snapshot["request_id"] == "first"
            assert len(gateway.requests) == 4
            gateway.responses.put(answer('{"recap":"检查完成，等待确认。"}'))
            owner.recap.start(expected_task=task, request_id="manual")
            assert owner.recap.wait(40)
            assert owner.recap.status()["text"] == "检查完成，等待确认。"
        finally:
            owner.close()
            assert owner.wait(10)
