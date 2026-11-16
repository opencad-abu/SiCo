"""Session-scoped Codex feature settings: read, save, revert and binding."""

import pytest
from sico.codex.backend import CodexBackend
from sico.codex import feature_groups
from sico.codex.feature_groups import CONFIGURED_EVENT
from sico.core.tools import ToolRegistry
from sico.storage.journal import SessionJournal
from test_agent_project import bound


def backend(journal, path):
    return CodexBackend(None, ToolRegistry(), journal, bound(path))


def test_feature_settings_read_save_and_revert(tmp_path):
    with SessionJournal(tmp_path, "features") as journal:
        agent = backend(journal, tmp_path)
        plain = agent.features.read()
        assert plain["bound"] is False and plain["overrides"] == {}
        assert {row["id"]: row for row in plain["groups"]}["view_image"]["options"] == {
            "features.view_image": True}

        saved = agent.features.save({"view_image": {"features.view_image": False}})
        assert saved["bound"] is False
        assert saved["overrides"] == {"view_image": {"features.view_image": False}}
        described = {row["id"]: row for row in saved["groups"]}
        assert described["view_image"]["overridden"] is True
        assert described["view_image"]["options"] == {"features.view_image": False}

        reverted = agent.features.save({})
        assert reverted["overrides"] == {}
        assert {row["id"]: row for row in reverted["groups"]}["view_image"]["options"] == {
            "features.view_image": True}

    with SessionJournal(tmp_path, "features") as journal:
        rows = [row for row in journal.events() if row["kind"] == CONFIGURED_EVENT]
        assert [row["payload"]["groups"] for row in rows] == [
            {"view_image": {"features.view_image": False}}, {}]


def test_feature_settings_are_session_scoped(tmp_path):
    with SessionJournal(tmp_path, "session-a") as first:
        one = backend(first, tmp_path)
        one.features.save({"view_image": {"features.view_image": False}})
        with SessionJournal(tmp_path, "session-b") as second:
            two = backend(second, tmp_path)
            assert two.features.read()["overrides"] == {}
    with SessionJournal(tmp_path, "session-a") as first:
        assert backend(first, tmp_path).features.read()["overrides"] == {
            "view_image": {"features.view_image": False}}
    with SessionJournal(tmp_path, "session-b") as second:
        assert backend(second, tmp_path).features.read()["overrides"] == {}


def test_feature_settings_refuse_after_runtime_binding(tmp_path):
    with SessionJournal(tmp_path, "bound") as journal:
        agent = backend(journal, tmp_path)
        agent.runtime = object()
        assert agent.features.read()["bound"] is True
        with pytest.raises(ValueError, match="new session"):
            agent.features.save({"view_image": {"features.view_image": False}})


def test_feature_settings_reject_invalid_groups(tmp_path):
    with SessionJournal(tmp_path, "invalid") as journal:
        agent = backend(journal, tmp_path)
        with pytest.raises(ValueError, match="unknown feature group"):
            agent.features.save({"nope": {"features.goals": False}})
        with pytest.raises(ValueError, match="fixed"):
            agent.features.save({"sandbox": {"sandbox_mode": "read-only"}})


def test_project_default_seeds_a_future_session(tmp_path):
    (tmp_path / ".sico").mkdir(mode=0o700)
    path = feature_groups.project_default_path(tmp_path / ".sico")
    feature_groups.write_project_default(
        path, {"view_image": {"features.view_image": False}})
    assert feature_groups.read_project_default(path) == {
        "view_image": {"features.view_image": False}}
    with SessionJournal(tmp_path, "project-default") as journal:
        agent = backend(journal, tmp_path)
        read = agent.features.read()
        assert read["source"] == "project" and read["bound"] is False
        described = {row["id"]: row for row in read["groups"]}
        assert described["view_image"]["options"] == {"features.view_image": False}
        # An explicit session choice wins and is retained for this session.
        agent.features.save({})
        assert agent.features.read()["source"] == "session"
        assert agent.features.read()["overrides"] == {}
    with SessionJournal(tmp_path, "project-default") as journal:
        assert backend(journal, tmp_path).features.read()["source"] == "session"


def test_project_default_file_is_validated(tmp_path):
    path = feature_groups.project_default_path(tmp_path / ".sico")
    path.parent.mkdir(parents=True)
    path.write_text('{"groups": {"nope": {"features.goals": false}}}')
    with pytest.raises(ValueError, match="unknown feature group"):
        feature_groups.read_project_default(path)
    path.write_text('{"wrong": {}}')
    with pytest.raises(ValueError, match="groups"):
        feature_groups.read_project_default(path)
