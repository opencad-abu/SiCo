"""Archived metadata commands against real Codex, without a model request."""

from contextlib import ExitStack, contextmanager

import pytest
from agent_service_business_fixtures import business_service
from agent_service_recovery_fixtures import client, seed
from sico.codex.backend import CodexBackend
from sico.codex.rpc import AppServer, RpcError, RpcRejected
from sico.core.tools import ToolRegistry
from sico.service.service_session_dto import SessionAddress
from sico.storage.journal import SessionJournal
from sico.storage.session_release import save_release
from test_agent_codex_native import gateway, native_codex, settings  # noqa: F401


@pytest.mark.parametrize("interrupted", [False, True])
def test_restarted_service_renames_and_deletes_original_native_thread_without_model_turn(
        tmp_path, monkeypatch, gateway, native_codex, interrupted):  # noqa: F811
    provider = settings(gateway, native_codex, "native")
    context, address = seed(tmp_path, monkeypatch, config=provider.options,
                            environment=provider.environment)
    with SessionJournal(tmp_path, "old") as journal:
        backend = CodexBackend(provider, ToolRegistry(), journal, context)
        try:
            events = list(backend.initialize_context(
                lambda: False, context=context, input_id="init"))
            assert events[-1]["kind"] == "task.completed"
            thread_id = backend.thread_id
        finally:
            backend.close()
        journal.append_session("session.end_requested", dict(address=address,
                               operation_id="old-end"), context)
        event = journal.append_session("session.end_observed", dict(address=address,
            operation_id="old-end", state="drained"), context)
        directory = journal.directory
    save_release(directory, SessionAddress.from_record(address), "old-end", event["sequence"])
    monkeypatch.setattr(CodexBackend, "_ensure_runtime",
                        lambda *_: pytest.fail("metadata must not start or resume a thread"))
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        monkeypatch.setattr(service.owner, "_capture_environment", lambda _: provider.environment)
        monkeypatch.setattr(service.owner, "_resolve_config", lambda *_: provider.options)
        api = client(stack, service, tmp_path)
        api.recovery.open_history("old").result(5)
        api.rename_session("old", "Archived native review").result(20)
        with SessionJournal(tmp_path, "old") as journal:
            backend = CodexBackend(provider, ToolRegistry(), journal, context)
            try:
                with backend._metadata_rpc() as rpc:
                    row = rpc.request("thread/read", dict(threadId=thread_id, includeTurns=False))
                    assert row["thread"]["name"] == "Archived native review"
            finally:
                backend.close()
        review = api.review_deletion("old").result(5)
        metadata = CodexBackend._metadata_rpc
        native_request, calls = AppServer.request, []
        def request(rpc, method, params, **kwargs):
            calls.append(method)
            return native_request(rpc, method, params, **kwargs)
        monkeypatch.setattr(AppServer, "request", request)
        @contextmanager
        def cleanup_failure(backend):
            with metadata(backend) as rpc:
                yield rpc
            raise RuntimeError("metadata cleanup interrupted")
        if interrupted:
            monkeypatch.setattr(CodexBackend, "_metadata_rpc", cleanup_failure)
            with pytest.raises(RuntimeError):
                api.delete_session("old", review_version=review["version"]).result(20)
            monkeypatch.setattr(CodexBackend, "_metadata_rpc", metadata)
        else:
            api.delete_session("old", review_version=review["version"]).result(20)
        assert service.dispatch.frontend.recovery._facts("old").deleted
        api.delete_session("old", review_version=review["version"]).result(20)
        assert calls == ["initialize", "thread/delete"]
        catalog = service.dispatch.frontend.workspace.catalog.snapshot()
        assert catalog["availability"]["old"] == "已删除"
        with SessionJournal(tmp_path, "old") as journal:
            backend = CodexBackend(provider, ToolRegistry(), journal, context)
            try:
                with backend._metadata_rpc() as rpc, pytest.raises(RpcError):
                    rpc.request("thread/read", dict(threadId=thread_id, includeTurns=False))
            finally:
                backend.close()
        # Codex itself rejects a second deletion: never infer success from a generic error code.
        with SessionJournal(tmp_path, "old") as journal:
            backend = CodexBackend(provider, ToolRegistry(), journal, context)
            try:
                with backend._metadata_rpc() as rpc, pytest.raises(RpcRejected):
                    rpc.request("thread/delete", dict(threadId=thread_id))
            finally:
                backend.close()
        assert not gateway.requests and not service.owner.controllers
