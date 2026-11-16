"""Manually removed session content becomes deletion-only without reconstructing it."""

import shutil
from contextlib import ExitStack

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from agent_service_recovery_fixtures import client
from sico.storage.history import CatalogIndex, SessionReader
from sico.storage.journal import SessionJournal
from sico.storage.record_removal import removed
from test_agent_service_continue_ended import ended
from test_sico_migration_history_policy import migrated  # noqa: F401


def damage(project, kind):
    directory = project / ".sico/ai/agent/sessions/old"
    if kind == "directory":
        shutil.rmtree(directory)
    elif kind == "empty":
        shutil.rmtree(directory)
        directory.mkdir(mode=0o700)
    elif kind == "log":
        (directory / "events.jsonl").unlink()
    elif kind == "truncated":
        (directory / "events.jsonl").write_bytes(b"")
    elif kind == "snapshot":
        (directory / "session.snapshot.json").unlink()
    else:
        path = directory / ("events.jsonl" if kind == "corrupt_log" else "session.snapshot.json")
        path.write_bytes(b"not-json\n")


@pytest.mark.parametrize("kind", ["directory", "empty", "log", "snapshot",
                                  "corrupt_log", "corrupt_snapshot", "truncated"])
def test_incomplete_history_is_delete_only_and_removal_survives_restart(
        tmp_path, monkeypatch, kind):
    ended(tmp_path, monkeypatch)
    root = tmp_path / ".sico/ai/agent"
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        before = api.recovery.inspect("old").result(5)
        service.dispatch.frontend.workspace.catalog.request().result(5)
        damage(tmp_path, kind)
        monkeypatch.setattr("sico.service.session_records.create_backend",
                            lambda *_a, **_kw: pytest.fail("damaged record must not start backend"))
        for operation in (lambda: api.preview_session("old"),
                          lambda: api.recovery.open_history("old"),
                          lambda: api.recovery.restore(before, "restore-damaged"),
                          lambda: api.recovery.continue_session("old"),
                          lambda: api.rename_session("old", "renamed")):
            with pytest.raises((ValueError, RuntimeError)):
                operation().result(5)
        review = api.review_deletion("old").result(5)
        assert review["damaged"] and review["requires_review"]
        if kind != "directory":
            catalog = service.dispatch.frontend.workspace.catalog
            catalog.request().result(5)
            row = next(r for r in catalog.snapshot()["rows"] if r["id"] == "old")
            assert row["damaged"]
        with pytest.raises(ValueError, match="确认删除"):
            api.delete_session("old").result(5)
        api.delete_session("old", review_version=review["version"]).result(5)
        api.delete_session("old", review_version=review["version"]).result(5)
        assert removed(root, "old") and "old" not in service.owner.controllers
        catalog = service.dispatch.frontend.workspace.catalog.snapshot()
        assert catalog["availability"]["old"] == "已删除"
        assert not any(row["id"] == "old" for row in catalog["rows"])
    with business_service(tmp_path, monkeypatch) as service:
        wait_until(lambda: not service.dispatch.frontend.recovery._scanning)
        assert service.dispatch.frontend.recovery.pending == 0
        assert CatalogIndex(root).scan()["old"]["unavailable"] == "已删除"
        with pytest.raises(ValueError, match="已删除"):
            SessionReader(root, "old")
        with pytest.raises(ValueError, match="已删除"):
            SessionJournal(tmp_path, "old")


def test_remnant_delete_retry_completes_catalog_after_lost_refresh(tmp_path, monkeypatch):
    from concurrent.futures import Future

    ended(tmp_path, monkeypatch)
    damage(tmp_path, "log")
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        review = api.review_deletion("old").result(5)
        catalog = service.dispatch.frontend.workspace.catalog
        original = catalog.request
        def interrupted():
            result = Future()
            result.set_exception(RuntimeError("catalog interrupted"))
            return result
        monkeypatch.setattr(catalog, "request", interrupted)
        with pytest.raises(RuntimeError):
            api.delete_session("old", review_version=review["version"]).result(5)
        monkeypatch.setattr(catalog, "request", original)
        # Even later changes to residual files cannot revive a committed deletion.
        directory = tmp_path / ".sico/ai/agent/sessions/old"
        (directory / "later-link").symlink_to(tmp_path)
        api.delete_session("old", review_version=review["version"]).result(5)
        assert catalog.snapshot()["availability"]["old"] == "已删除"


@pytest.mark.parametrize("change", ["changed", "repaired", "writer", "active", "symlink"])
def test_remnant_confirmation_rechecks_revision_and_ownership(tmp_path, monkeypatch, change):
    context, _ = ended(tmp_path, monkeypatch)
    directory = tmp_path / ".sico/ai/agent/sessions/old"
    saved = (directory / "events.jsonl").read_bytes()
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, tmp_path)
        damage(tmp_path, "log")
        review = api.review_deletion("old").result(5)
        if change == "changed":
            path = directory / "another-file"
            path.write_bytes(b"changed")
            path.chmod(0o600)
        elif change == "repaired":
            (directory / "events.jsonl").write_bytes(saved)
            (directory / "events.jsonl").chmod(0o600)
        elif change == "writer":
            from sico.storage.record_removal import writer_lock

            stack.enter_context(writer_lock(directory.parent.parent, "old"))
        elif change == "active":
            service.owner.open("old", {}, context)
        else:
            (directory / "outside").symlink_to(tmp_path)
        with pytest.raises((ValueError, RuntimeError)):
            api.delete_session("old", review_version=review["version"]).result(5)
        assert not removed(directory.parent.parent, "old")


def test_missing_migrated_content_can_only_be_discarded(migrated, monkeypatch):  # noqa: F811
    from sico.storage.history_policy import NAME

    project, _context, _address, _legacy, _report = migrated
    root = project / ".sico/ai/agent"
    (root / "sessions/old/events.jsonl").unlink()
    policy = (root / NAME).read_bytes()
    with business_service(project, monkeypatch) as service, ExitStack() as stack:
        api = client(stack, service, project)
        with pytest.raises((ValueError, RuntimeError)):
            api.recovery.open_history("old").result(5)
        review = api.review_deletion("old").result(5)
        assert review["damaged"]
        api.delete_session("old", review_version=review["version"]).result(5)
        assert removed(root, "old")
    assert (root / NAME).read_bytes() == policy
