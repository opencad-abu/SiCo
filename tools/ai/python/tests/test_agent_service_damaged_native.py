"""Deleting native session files makes the intact SiCo row deletion-only."""

import shutil
from contextlib import ExitStack

import pytest
from agent_service_business_fixtures import business_service
from agent_service_recovery_fixtures import client, seed
from sico.codex.backend import CodexBackend
from sico.core.tools import ToolRegistry
from sico.service.service_session_dto import SessionAddress
from sico.storage.history import CatalogIndex
from sico.storage.journal import SessionJournal
from sico.storage.record_removal import removed
from sico.storage.session_release import save_release
from test_agent_codex_native import gateway, native_codex, settings  # noqa: F401


@pytest.mark.parametrize("part", ["home", "sessions", "rollout"])
def test_missing_native_content_cannot_preview_or_resume_and_can_be_discarded(
        tmp_path, monkeypatch, gateway, native_codex, part):  # noqa: F811
    provider = settings(gateway, native_codex, "native")
    context, address = seed(tmp_path, monkeypatch, config=provider.options,
                            environment=provider.environment)
    with SessionJournal(tmp_path, "old") as journal:
        backend = CodexBackend(provider, ToolRegistry(), journal, context)
        try:
            assert list(backend.initialize_context(lambda: False, context=context,
                        input_id="init"))[-1]["kind"] == "task.completed"
        finally:
            backend.close()
        event = journal.append_session("session.end_observed", dict(address=address,
            operation_id="old-end", state="drained"), context)
        directory, root = journal.directory, journal.root
    save_release(directory, SessionAddress.from_record(address), "old-end", event["sequence"])
    catalog = CatalogIndex(root)
    assert not catalog.scan()["old"].get("unavailable")
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        view = api.recovery.inspect("old").result(5)
        preview = api.preview_session("old").result(5)
        home = directory / "codex"
        if part == "rollout":
            next((home / "sessions").glob("*/*/*/rollout-*.jsonl")).unlink()
        else:
            shutil.rmtree(home if part == "home" else home / "sessions")
        assert catalog.scan()["old"]["damaged"]
        monkeypatch.setattr("sico.service.session_records.create_backend",
                            lambda *_a, **_kw: pytest.fail("must not recreate native history"))
        for operation in (lambda: api.preview_session("old"),
                          lambda: api.recovery.restore(view, "restore-damaged"),
                          lambda: api.recovery.continue_session("old"),
                          lambda: api.event_batch(preview.stream)):
            with pytest.raises((ValueError, RuntimeError)):
                operation().result(5)
        review = api.review_deletion("old").result(5)
        assert review["damaged"]
        api.delete_session("old", review_version=review["version"]).result(5)
        assert removed(root, "old") and not gateway.requests
