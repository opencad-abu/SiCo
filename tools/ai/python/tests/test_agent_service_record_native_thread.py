"""Archived Codex records use metadata-only native commands without resuming a task."""

from contextlib import ExitStack, contextmanager

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from agent_service_gui_fixtures import remote_window
from agent_service_recovery_fixtures import client, seed
from sico.codex.backend import CodexBackend
from sico.codex.settings import CodexSettings
from sico.core.contracts import RunState
from sico.service.service_session_dto import SessionAddress
from sico.service.session_dependencies import SessionDependencies
from sico.storage.journal import SessionJournal
from sico.storage.session_release import save_release
from sico.storage.session_snapshot import capture, save


def test_archived_thread_delete_uses_saved_thread_and_preserves_evidence_on_failure(
        tmp_path, monkeypatch):
    config = {'backend': 'codex', 'provider': 'responses', 'base_url': 'https://example.invalid/v1',
              'model': 'test', 'api_key_env': 'SICO_API_KEY'}
    with remote_window(tmp_path, monkeypatch) as (service, window):
        token = window.page.session
        context = window.page.view.context
        controller = service.owner.controllers['live']
        controller.journal.append('codex.thread', {'thread_id': 'native-thread'},
                                  controller.journal.state or RunState(context))
        # Capture a real Codex config without constructing a network provider during session open.
        bundle = service.owner._bundles['live']
        environment = {'SICO_API_KEY': 'test-secret'}
        bundle.dependencies = SessionDependencies(config, environment, {})
        save(controller.journal.directory, capture('live', context, config, environment, {},
                                                   service.descriptor.project_id))
        window.api.end_session(token, 'native-record-end').result(5)
        wait_until(lambda: not service.owner.controllers)
        calls = []
        failing = [True]
        class Rpc:
            def request(self, method, params, **kwargs):
                calls.append((method, params))
                if failing[0]:
                    raise ValueError('native deletion refused')
                return {}
        @contextmanager
        def metadata(backend):
            assert isinstance(backend.provider, CodexSettings)
            assert backend.thread_id == 'native-thread'
            assert not backend.tools.schemas() and backend.runtime is None
            yield Rpc()
        monkeypatch.setattr(CodexBackend, '_metadata_rpc', metadata)
        monkeypatch.setattr(CodexBackend, '_ensure_runtime',
                            lambda *_: pytest.fail('metadata operation must not resume a task'))
        with pytest.raises(ValueError, match='native deletion refused'):
            window.api.delete_session('live').result(5)
        assert not service.dispatch.frontend.recovery._facts('live').deleted
        failing[0] = False
        window.api.delete_session('live').result(5)
        assert calls == [('thread/delete', {'threadId': 'native-thread'})] * 2
        assert service.dispatch.frontend.recovery._facts('live').deleted
        assert not service.owner.controllers


@pytest.mark.parametrize("action", ["delete", "rename"])
@pytest.mark.parametrize("source", ["active", "configured", "missing", "different_endpoint"])
def test_old_thread_metadata_after_service_restart_uses_matching_credentials(
        tmp_path, monkeypatch, action, source):
    config = dict(backend="codex", provider="responses", base_url="https://example.invalid",
                  model="old-model", api_key_env="SICO_API_KEY")
    context, address = seed(tmp_path, monkeypatch, config=config,
                            environment={"SICO_API_KEY": "old-secret"})
    with SessionJournal(tmp_path, "old") as journal:
        journal.append("codex.thread", dict(thread_id="original-native-thread"), RunState(context))
        journal.append_session("session.end_requested", dict(address=address,
                               operation_id="old-end"), context)
        ended = journal.append_session("session.end_observed", dict(address=address,
            operation_id="old-end", state="drained"), context)
        directory = journal.directory
    save_release(directory, SessionAddress.from_record(address), "old-end", ended["sequence"])
    snapshot = (directory / "session.snapshot.json").read_bytes()
    calls = []
    @contextmanager
    def metadata(backend):
        assert backend.provider.api_key == "current-secret"
        assert backend.provider.model == "old-model"
        assert backend.thread_id == "original-native-thread" and backend.runtime is None
        class Rpc:
            def request(self, method, params, **_kw):
                calls.append((method, params))
                return {}
        yield Rpc()
    monkeypatch.setattr(CodexBackend, "_metadata_rpc", metadata)
    monkeypatch.setattr(CodexBackend, "_ensure_runtime",
                        lambda *_: pytest.fail("metadata must not resume a task"))
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        selected = dict(config, model="current-model", base_url="https://example.invalid/v1/")
        if source == "different_endpoint":
            selected["base_url"] = "https://other.invalid/v1"
        if source == "active":
            service.owner.open("current", {}, context, provider_config=selected,
                               environment={"SICO_API_KEY": "current-secret"})
        elif source != "missing":
            monkeypatch.setattr(service.owner, "_capture_environment",
                                lambda _: {"SICO_API_KEY": "current-secret"})
            monkeypatch.setattr(service.owner, "_resolve_config", lambda *_: selected)
        api = client(stack, service, tmp_path)
        api.recovery.open_history("old").result(5)
        def request():
            return (api.delete_session("old") if action == "delete" else
                    api.rename_session("old", "Reviewed name"))
        if source in {"missing", "different_endpoint"}:
            with pytest.raises(ValueError, match="当前模型连接不可用"):
                request().result(5)
            assert not calls and not service.dispatch.frontend.recovery._facts("old").deleted
        else:
            request().result(5)
            assert calls[0][0] == ("thread/delete" if action == "delete" else "thread/name/set")
            assert calls[0][1]["threadId"] == "original-native-thread"
        assert (directory / "session.snapshot.json").read_bytes() == snapshot
        assert b"current-secret" not in (directory / "events.jsonl").read_bytes()
        assert "old" not in service.owner.controllers and not service.provider.seen
