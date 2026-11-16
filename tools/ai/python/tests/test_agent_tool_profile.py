"""Session tool-profile setting: selection, migration and restore."""

import json

import pytest
from sico.agent_resources import mode
from sico.codex.backend import CodexBackend
from sico.codex.profile_setting import EVENT, SCOPE_EVENT, read_project_default, record_scope
from sico.core.contracts import RunState
from sico.core.tools import ToolRegistry
from sico.storage.journal import SessionJournal
from test_agent_codex_native import gateway as gateway  # noqa: F401  (fixture)
from test_agent_codex_native import native_codex as native_codex  # noqa: F401  (fixture)
from test_agent_project import bound


def backend(journal, path):
    return CodexBackend(None, ToolRegistry(), journal, bound(path))


def profile_events(journal):
    return [row for row in journal.events() if row["kind"] == EVENT]


def write_project_default(path, profile):
    state = path / ".sico"
    state.mkdir(mode=0o700, exist_ok=True)
    target = state / "ai" / "tool-profile.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"default": profile}))
    return target


def test_fresh_session_records_the_product_default(tmp_path):
    with SessionJournal(tmp_path, "fresh") as journal:
        agent = backend(journal, tmp_path)
        assert agent.tool_profile.profile == "desktop_compat"
        assert agent.tool_profile.source == "automatic"
    with SessionJournal(tmp_path, "fresh") as journal:
        rows = profile_events(journal)
        assert len(rows) == 1
        assert rows[0]["payload"] == {"profile": "desktop_compat", "source": "automatic"}


def test_legacy_project_default_cannot_narrow_a_new_session(tmp_path):
    target = write_project_default(tmp_path, "circuit")
    assert read_project_default(target) == "circuit"
    with SessionJournal(tmp_path, "project-default") as journal:
        agent = backend(journal, tmp_path)
        assert agent.tool_profile.profile == "desktop_compat"
        assert agent.tool_profile.source == "automatic"


def test_agent_mode_is_write_authorization_not_a_profile_choice(tmp_path):
    (tmp_path / ".sico").mkdir(mode=0o700)
    mode.save(tmp_path, "generation", expected_revision=None)
    with SessionJournal(tmp_path, "generation") as journal:
        agent = backend(journal, tmp_path)
        assert agent.tool_profile.profile == "desktop_compat"
        assert agent.tool_profile.source == "automatic"


def test_legacy_session_without_a_profile_migrates_to_automatic(tmp_path):
    with SessionJournal(tmp_path, "legacy") as journal:
        journal.append("codex.thread",
                       {"thread_id": "t-1", "version": "0", "history_mode": "legacy",
                        "capability_roots": []}, RunState(bound(tmp_path)))
        journal.append("task.started", {
            "text": "hi", "context": bound(tmp_path).record(), "input_id": "i1",
            "origin": "chat", "attachments": [], "inputs": [], "turn_options": {},
        }, RunState(bound(tmp_path)))
        agent = backend(journal, tmp_path)
        assert agent.tool_profile.profile == "desktop_compat"
        assert agent.tool_profile.source == "automatic-migration"
    with SessionJournal(tmp_path, "legacy") as journal:
        rows = profile_events(journal)
        assert rows[-1]["payload"]["source"] == "automatic-migration"


def test_restored_product_profile_is_migrated_to_automatic(tmp_path):
    with SessionJournal(tmp_path, "restore-product") as journal:
        journal.append(EVENT, {"profile": "mixed_design", "source": "project"},
                       RunState(bound(tmp_path)))
    with SessionJournal(tmp_path, "restore-product") as journal:
        agent = backend(journal, tmp_path)
        assert agent.tool_profile.profile == "desktop_compat"
        assert agent.tool_profile.source == "automatic-migration"
        assert profile_events(journal)[-1]["payload"] == {
            "profile": "desktop_compat", "source": "automatic-migration",
            "previous": "mixed_design"}


def test_restore_keeps_an_explicit_session_profile(tmp_path):
    with SessionJournal(tmp_path, "restore") as journal:
        journal.append(EVENT, {"profile": "circuit", "source": "session"},
                       RunState(bound(tmp_path)))
    with SessionJournal(tmp_path, "restore") as journal:
        agent = backend(journal, tmp_path)
        assert agent.tool_profile.profile == "circuit"
        assert agent.tool_profile.source == "session"
        # Restoring never re-resolves defaults or duplicates the record.
        assert len(profile_events(journal)) == 1


def test_select_is_explicit_and_refuses_after_the_runtime_is_bound(tmp_path):
    with SessionJournal(tmp_path, "select") as journal:
        agent = backend(journal, tmp_path)
        assert agent.tool_profile.select("simulation") == "simulation"
        assert agent.tool_profile.profile == "simulation"
        assert profile_events(journal)[-1]["payload"] == {
            "profile": "simulation", "source": "session"}
        agent.tool_profile.select("simulation")          # idempotent
        assert len(profile_events(journal)) == 2
        with pytest.raises(ValueError, match="unknown tool profile"):
            agent.tool_profile.select("no_such_profile")
        agent.runtime = object()
        with pytest.raises(ValueError, match="new session"):
            agent.tool_profile.select("circuit")
        assert agent.tool_profile.profile == "simulation"


def test_malformed_project_default_fails_loudly(tmp_path):
    target = write_project_default(tmp_path, "circuit")
    target.write_text(json.dumps({"default": "nope"}))
    with pytest.raises(ValueError, match="unknown tool profile"):
        read_project_default(target)
    target.write_text(json.dumps({"profile": "circuit"}))
    with pytest.raises(ValueError, match="default"):
        read_project_default(target)
    target.write_text("{not json")
    with pytest.raises(ValueError, match="invalid"):
        read_project_default(target)


def test_profile_choice_is_session_scoped(tmp_path):
    with SessionJournal(tmp_path, "session-a") as first_journal:
        first = backend(first_journal, tmp_path)
        assert first.tool_profile.select("circuit") == "circuit"
        with SessionJournal(tmp_path, "session-b") as second_journal:
            second = backend(second_journal, tmp_path)
            assert second.tool_profile.profile == "desktop_compat"
            assert second.tool_profile.select("simulation") == "simulation"
            assert first.tool_profile.profile == "circuit"
    # Each session restores only its own recorded choice.
    with SessionJournal(tmp_path, "session-a") as journal:
        assert backend(journal, tmp_path).tool_profile.profile == "circuit"
    with SessionJournal(tmp_path, "session-b") as journal:
        assert backend(journal, tmp_path).tool_profile.profile == "simulation"


def test_effective_scope_is_recorded_and_verified_on_restore(tmp_path):
    with SessionJournal(tmp_path, "scope-log") as journal:
        agent = backend(journal, tmp_path)
        scope = {"profile": "desktop_compat", "tools": ("a", "b"),
                 "digest": "sha256:first"}
        first = record_scope(agent, scope)
        assert first["match"] is False and first["changed"] is False
        assert first["prior_digest"] is None and first["count"] == 2
        same = record_scope(agent, scope)
        assert same["match"] is True and same["changed"] is False
        # A resumed session whose resolved catalog moved is recorded, not hidden.
        moved = record_scope(agent, {"profile": "desktop_compat",
                                     "tools": ("a", "b", "c"),
                                     "digest": "sha256:second"})
        assert moved["changed"] is True and moved["match"] is False
        assert moved["prior_digest"] == "sha256:first"
    with SessionJournal(tmp_path, "scope-log") as journal:
        rows = [row for row in journal.events() if row["kind"] == SCOPE_EVENT]
        assert [row["payload"]["digest"] for row in rows] == [
            "sha256:first", "sha256:first", "sha256:second"]


def test_native_assembly_records_and_reverifies_the_scope(
        tmp_path, gateway, native_codex):
    from test_agent_codex_native import CONTEXT, answer, registry, settings

    with SessionJournal(tmp_path, "native-scope") as journal:
        backend = CodexBackend(settings(gateway, native_codex, "native"),
                               registry([]), journal, CONTEXT)
        try:
            gateway.responses.put(answer("done"))
            assert list(backend.run("scope"))[-1]["kind"] == "task.completed"
            records = [row["payload"] for row in journal.events()
                       if row["kind"] == SCOPE_EVENT]
            assert len(records) == 1
            assert records[0]["profile"] == "desktop_compat"
            assert records[0]["match"] is False
        finally:
            backend.close()
        restored = CodexBackend(settings(gateway, native_codex, "native"),
                                registry([]), journal, CONTEXT)
        try:
            restored.resume_thread()
            records = [row["payload"] for row in journal.events()
                       if row["kind"] == SCOPE_EVENT]
            assert len(records) == 2
            assert records[1]["match"] is True and records[1]["changed"] is False
            assert records[1]["prior_digest"] == records[0]["digest"]
        finally:
            restored.close()
