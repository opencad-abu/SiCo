"""Project resource settings stay scoped, revision-checked and non-executing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from agent_settings_boundary_fixtures import (
    RUNTIME,
    SERVICE,
    SESSION,
    VERSION,
    Hub,
    Worker,
    address,
    reader,
    selected_snapshot,
)
from sico.agent_resources import mode, model, store
from sico.agent_resources import snapshot as resource_snapshot
from sico.service import agent_settings
from sico.service.frontend_session import SessionToken
from sico.service.service_session_dto import SessionAddress
from sico.transport.framing import ProtocolError


def test_scope_requires_the_same_service_and_resolves_live_sessions():
    hub = Hub("project")
    assert agent_settings.scope(hub, None) is None
    live = address(RUNTIME).record()
    assert agent_settings.scope(hub, live).session.runtime_id == RUNTIME
    assert hub.controlled == [live]
    assert agent_settings.scope(hub, address().record()).session.runtime_id is None
    assert hub.controlled == [live]

    foreign = SessionAddress("e" * 32, SERVICE, SessionToken(SESSION, None)).record()
    with pytest.raises(ProtocolError, match="another project"):
        agent_settings.scope(hub, foreign)


def test_observed_projects_only_valid_documents(monkeypatch):
    assert agent_settings.observed(lambda: {"manifest": {"version": 1}}) == {
        "status": "available",
        "manifest": {"version": 2, "skill_roots": [], "plugins": [],
                     "capability_roots": [], "mcp_servers": [], "knowledge_sources": [],
                     "skill_overrides": []},
    }
    for error in (ValueError("bad"), OSError("gone"), RuntimeError("closed")):
        assert agent_settings.observed(
            lambda error=error: (_ for _ in ()).throw(error)
        ) == {"status": "unavailable", "message": "配置不可读或格式无效，请核对原文件"}


def test_selected_defaults_choose_provider_selection_or_project_defaults(monkeypatch):
    hub = Hub("project")
    monkeypatch.setattr(agent_settings, "read_selection",
                        lambda provider, project: {
                            "provider": provider, "project": project,
                            "manifest": {"version": 1}})
    assert agent_settings.selected_defaults(hub, address(RUNTIME)) == {
        "provider": {"session": SESSION}, "project": "project",
        "manifest": {"version": 1},
    }
    assert agent_settings.selected_defaults(hub, None) == hub.owner.defaults
    assert hub.owner.default_calls == 1


def test_session_view_for_live_worker_is_recorded_as_unrecorded(monkeypatch):
    hub = Hub("project")
    hub.workspace.worker = Worker("resources")
    live = address(RUNTIME)
    assert agent_settings.session_view(hub, live) == {
        "status": "available", "recorded": False, "snapshot": "resources",
    }
    assert hub.workspace.worker.calls == [(live.session, "resource_status")]
    assert hub.controlled == [live.record()]

    for error in (ValueError("bad"), RuntimeError("closed"), OSError("gone")):
        hub.workspace.worker = Worker(error=error)
        assert agent_settings.session_view(hub, live) == {
            "status": "unavailable",
            "message": "会话资源无法检查，工程默认仍可管理",
        }
    assert agent_settings.session_view(hub, None) is None


def test_session_view_rejects_conflict_missing_and_invalid_history(monkeypatch):
    hub = Hub("project")
    first = selected_snapshot()
    monkeypatch.setattr(agent_settings, "SessionReader", reader([
        {"kind": resource_snapshot.EVENT, "payload": first},
        {"kind": resource_snapshot.EVENT, "payload": dict(first, origin="legacy")},
    ]))
    assert agent_settings.session_view(hub, address()) == {
        "status": "unavailable", "message": "历史资源选择快照存在冲突",
    }

    monkeypatch.setattr(agent_settings, "SessionReader", reader([]))
    assert agent_settings.session_view(hub, address()) == {
        "status": "unavailable", "message": "此历史会话没有资源选择快照",
    }

    monkeypatch.setattr(agent_settings, "SessionReader", reader([
        {"kind": resource_snapshot.EVENT, "payload": {"version": 1}},
    ]))
    assert agent_settings.session_view(hub, address()) == {
        "status": "unavailable", "message": "历史资源选择快照无效",
    }


def test_session_view_freezes_the_last_inventory(monkeypatch):
    hub = Hub("project")
    value = selected_snapshot()
    monkeypatch.setattr(agent_settings, "SessionReader", reader([
        {"kind": "codex.resources.probed", "payload": {}},
        {"kind": "codex.resources.updated", "payload": {"inventory": ["first"]}},
        {"kind": resource_snapshot.EVENT, "payload": value},
        {"kind": "codex.resources.updated", "payload": {"inventory": ["last"]}},
    ]))
    result = agent_settings.session_view(hub, address())
    assert result["status"] == "available" and result["recorded"] is True
    assert result["snapshot"] == {
        "inventory": ["last"],
        "configuration": {
            "state": "frozen",
            "effective": value["selection"],
            "origin": "defaults",
            "drift": [],
        },
    }


def test_session_data_sources_reads_live_dependencies_and_bounded_history(
        monkeypatch):
    hub = Hub("project")
    hub.owner.dependencies = {"environment": {"SICO_PDK_DATA": "/pdk"}}
    monkeypatch.setattr(agent_settings, "effective_data_sources",
                        lambda environment, workspace, *, project=None: {
                            "environment": environment, "workspace": workspace})
    assert agent_settings._session_data_sources(hub, address(RUNTIME)) == {
        "status": "available",
        "values": {"environment": {"SICO_PDK_DATA": "/pdk"}, "workspace": "project"},
    }
    assert agent_settings._session_data_sources(hub, None) is None

    hub.owner.dependencies = {"environment": {}}
    monkeypatch.setattr(agent_settings, "SessionReader", reader([]))
    monkeypatch.setattr(agent_settings.session_snapshot, "read",
                        lambda _directory: {"environment": {"SICO_PDK_DATA": "/saved"}})
    assert agent_settings._session_data_sources(hub, address()) == {
        "status": "available",
        "values": {"environment": {"SICO_PDK_DATA": "/saved"}, "workspace": "project"},
    }

    monkeypatch.setattr(agent_settings.session_snapshot, "read", lambda _directory: None)
    assert agent_settings._session_data_sources(hub, address()) == {
        "status": "unavailable", "message": "历史会话未记录数据源路径",
    }
    monkeypatch.setattr(agent_settings.session_snapshot, "read",
                        lambda _directory: {"environment": {"UNRELATED": "x"}})
    assert agent_settings._session_data_sources(hub, address()) == {
        "status": "unavailable", "message": "历史会话未记录数据源路径",
    }


@pytest.mark.parametrize("failure", ["dependencies", "snapshot", "effective"])
def test_session_data_sources_never_leaks_unreadable_environment(monkeypatch, failure):
    hub = Hub("project")
    if failure == "dependencies":
        hub.owner.dependencies_for = lambda _session: (_ for _ in ()).throw(
            ValueError("bad dependency"))
    else:
        monkeypatch.setattr(agent_settings, "SessionReader", reader([]))
        if failure == "snapshot":
            monkeypatch.setattr(agent_settings.session_snapshot, "read",
                                lambda _directory: (_ for _ in ()).throw(
                                    OSError("gone")))
        else:
            monkeypatch.setattr(agent_settings.session_snapshot, "read",
                                lambda _directory: {
                                    "environment": {"SICO_PDK_DATA": "/pdk"}})
            monkeypatch.setattr(agent_settings, "effective_data_sources",
                                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                                    RuntimeError("closed")))
    assert agent_settings._session_data_sources(
        hub, address(RUNTIME if failure == "effective" else None)
    )["status"] == "unavailable"


def test_data_sources_view_exposes_project_defaults_without_reading_secrets(
        tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    document = {"path": str(tmp_path / "sources.json"), "revision": VERSION,
                "editable": True, "pdk_data": "/pdk", "circuit_templates": "/templates"}
    monkeypatch.setattr(agent_settings, "agent_data", SimpleNamespace(
        read=lambda _project: document,
        default_path=lambda _project: tmp_path / "sources.json",
    ))
    monkeypatch.setattr(agent_settings, "effective_data_sources",
                        lambda _environment, _project, project: {
                            "project": project["pdk_data"]})
    monkeypatch.setattr(agent_settings, "_session_data_sources",
                        lambda _hub, _address: {"status": "available"})
    result = agent_settings.data_sources_view(hub, None)
    assert result["status"] == "available" and result["message"] == ""
    assert result["project"] == {
        "pdk_data": "/pdk", "circuit_templates": "/templates"}
    assert result["effective"] == {"project": "/pdk"}
    assert result["session"] == {"status": "available"}


def test_data_sources_view_marks_unreadable_documents_and_effective_failure(
        tmp_path, monkeypatch):
    hub = Hub(tmp_path)
    monkeypatch.setattr(agent_settings, "agent_data", SimpleNamespace(
        read=lambda _project: (_ for _ in ()).throw(OSError("bad file")),
        default_path=lambda _project: tmp_path / "sources.json",
    ))
    monkeypatch.setattr(agent_settings, "effective_data_sources",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            RuntimeError("closed")))
    monkeypatch.setattr(agent_settings, "_session_data_sources",
                        lambda _hub, _address: None)
    result = agent_settings.data_sources_view(hub, None)
    assert result["status"] == "unavailable" and "bad file" in result["message"]
    assert result["revision"] is None and result["editable"] is False
    assert result["project"] == {"pdk_data": None, "circuit_templates": None}
    assert result["effective"] == {}


def test_read_assembles_available_and_default_sections(monkeypatch):
    hub = Hub("project")
    monkeypatch.setattr(store, "read_project", lambda _project: {
        "manifest": {"version": 1}, "source": "project", "revision": None,
        "editable": True})
    monkeypatch.setattr(agent_settings, "session_view", lambda *_args: {"session": 1})
    monkeypatch.setattr(agent_settings, "data_sources_view",
                        lambda *_args: {"data": 1})
    monkeypatch.setattr(mode, "read", lambda _project: {
        "path": "/mode", "pdk_workflow": "design", "revision": None, "editable": True})
    monkeypatch.setattr(model, "read", lambda _project: {
        "path": "/model", "backend": "codex", "provider": "responses",
        "revision": None, "editable": True})
    result = agent_settings.read(hub, {"address": None})
    assert result["session"] == {"session": 1}
    assert result["project_defaults"]["status"] == "available"
    assert result["selected_defaults"]["status"] == "available"
    assert result["pdk_workflow"]["status"] == "available"
    assert result["model_settings"]["status"] == "available"
    assert result["data_sources"] == {"data": 1}
    assert result["defaults_scope"] == "service"
    assert result["project"] == "project"


def test_read_reports_each_unavailable_source_and_keeps_scope_checked(monkeypatch):
    hub = Hub("project")
    monkeypatch.setattr(store, "read_project", lambda _project: {
        "manifest": {"version": 1}, "source": "project", "revision": None,
        "editable": True})
    monkeypatch.setattr(agent_settings, "session_view",
                        lambda *_args: (_ for _ in ()).throw(TypeError("bad")))
    monkeypatch.setattr(agent_settings, "data_sources_view", lambda *_args: None)
    monkeypatch.setattr(mode, "read",
                        lambda _project: (_ for _ in ()).throw(OSError("gone")))
    monkeypatch.setattr(model, "read",
                        lambda _project: (_ for _ in ()).throw(ValueError("bad")))
    result = agent_settings.read(hub, {"address": address(RUNTIME).record()})
    assert result["session"]["status"] == "unavailable"
    assert result["pdk_workflow"]["status"] == "unavailable"
    assert result["model_settings"]["status"] == "unavailable"
    assert result["defaults_scope"] == "session"
    assert len(hub.controlled) == 2

    with pytest.raises(ProtocolError):
        agent_settings.read(hub, {"address": None, "extra": True})


def test_read_rechecks_scope_after_slow_reads(monkeypatch):
    hub = Hub("project")
    monkeypatch.setattr(store, "read_project", lambda _project: {
        "manifest": {"version": 1}, "source": "project", "revision": None,
        "editable": True})
    monkeypatch.setattr(agent_settings, "session_view", lambda *_args: None)
    monkeypatch.setattr(agent_settings, "data_sources_view", lambda *_args: None)
    monkeypatch.setattr(mode, "read", lambda _project: {
        "path": "/mode", "pdk_workflow": "design", "revision": None, "editable": True})
    monkeypatch.setattr(model, "read", lambda _project: {
        "path": "/model", "backend": "codex", "provider": "responses",
        "revision": None, "editable": True})
    calls = {"count": 0}

    def controller(address):
        calls["count"] += 1
        hub.controlled.append(address)
        if calls["count"] == 2:
            raise ProtocolError("session ended")

    hub.controller = controller
    with pytest.raises(ProtocolError, match="session ended"):
        agent_settings.read(hub, {"address": address(RUNTIME).record()})
