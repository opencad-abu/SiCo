"""Project resource saves report every conflict and durability outcome explicitly."""

from __future__ import annotations

import pytest
from agent_settings_boundary_fixtures import VERSION, Hub, error_type
from sico.agent_resources import data_sources as agent_data
from sico.agent_resources import mode, model, store
from sico.service import agent_settings
from sico.transport.framing import ProtocolError


@pytest.mark.parametrize("function,params", [
    (agent_settings.save, {"manifest": {"version": 1}, "expected_revision": None}),
    (agent_settings.save_mode, {"pdk_workflow": "design", "expected_revision": None}),
    (agent_settings.save_model, {"backend": "python", "provider": "openai",
                                 "expected_revision": None}),
    (agent_settings.save_data_sources,
     {"pdk_data": "/pdk", "circuit_templates": None, "expected_revision": None}),
])
def test_save_functions_accept_only_their_documented_fields(function, params):
    with pytest.raises(ProtocolError):
        function(Hub("project"), dict({"address": None, "unexpected": True}, **params))


@pytest.mark.parametrize("error_name,outcome", [
    ("ResourceConflictError", "conflict"),
    ("ResourceSaveUncertainError", "uncertain"),
    ("BlockingIOError", "rejected"),
    ("ValueError", "rejected"),
    ("OSError", "uncertain"),
    ("RuntimeError", "uncertain"),
])
def test_project_resource_errors_map_to_explicit_outcomes(
        monkeypatch, error_name, outcome):
    error = error_type(store, error_name)
    monkeypatch.setattr(store, "save_project",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(error("bad")))
    result = agent_settings.save(Hub("project"), {
        "address": None, "manifest": {"version": 1}, "expected_revision": None})
    assert result["outcome"] == outcome
    if error_name == "ValueError":
        assert result["message"] == "bad"


@pytest.mark.parametrize("error_name,outcome", [
    ("ModeConflictError", "conflict"),
    ("ModeSaveUncertainError", "uncertain"),
    ("BlockingIOError", "rejected"),
    ("ValueError", "rejected"),
    ("OSError", "uncertain"),
    ("RuntimeError", "uncertain"),
])
def test_mode_save_errors_map_to_explicit_outcomes(monkeypatch, error_name, outcome):
    error = error_type(mode, error_name)
    monkeypatch.setattr(mode, "save",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(error("bad")))
    result = agent_settings.save_mode(Hub("project"), {
        "address": None, "pdk_workflow": "design", "expected_revision": None})
    assert result["outcome"] == outcome


@pytest.mark.parametrize("error_name,outcome", [
    ("ModelConflictError", "conflict"),
    ("ModelSaveUncertainError", "uncertain"),
    ("BlockingIOError", "rejected"),
    ("ValueError", "rejected"),
    ("OSError", "uncertain"),
    ("RuntimeError", "uncertain"),
])
def test_model_save_errors_map_to_explicit_outcomes(monkeypatch, error_name, outcome):
    error = error_type(model, error_name)
    monkeypatch.setattr(model, "save",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(error("bad")))
    result = agent_settings.save_model(Hub("project"), {
        "address": None, "backend": "python", "provider": "openai",
        "expected_revision": None})
    assert result["outcome"] == outcome


@pytest.mark.parametrize("error_name,outcome", [
    ("DataSourcesConflictError", "conflict"),
    ("DataSourcesSaveUncertainError", "uncertain"),
    ("BlockingIOError", "rejected"),
    ("ValueError", "rejected"),
    ("OSError", "uncertain"),
    ("RuntimeError", "uncertain"),
])
def test_data_source_save_errors_map_to_explicit_outcomes(
        monkeypatch, error_name, outcome):
    error = error_type(agent_data, error_name)
    monkeypatch.setattr(agent_data, "save",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(error("bad")))
    result = agent_settings.save_data_sources(Hub("project"), {
        "address": None, "pdk_data": "/pdk", "circuit_templates": None,
        "expected_revision": None})
    assert result["outcome"] == outcome


def test_save_success_shapes_documents(monkeypatch):
    hub = Hub("project")
    monkeypatch.setattr(store, "save_project", lambda *_args, **_kwargs: {
        "manifest": {"version": 1}, "revision": VERSION})
    assert agent_settings.save(hub, {
        "address": None, "manifest": {"version": 1}, "expected_revision": None,
    })["document"]["manifest"]["version"] == 2

    monkeypatch.setattr(mode, "save", lambda *_args, **_kwargs: {
        "pdk_workflow": "design", "revision": VERSION})
    assert agent_settings.save_mode(hub, {
        "address": None, "pdk_workflow": "design", "expected_revision": None,
    })["document"] == {"status": "available", "pdk_workflow": "design",
                       "revision": VERSION}

    monkeypatch.setattr(model, "save", lambda *_args, **_kwargs: {
        "backend": "python", "provider": "openai", "revision": VERSION})
    assert agent_settings.save_model(hub, {
        "address": None, "backend": "python", "provider": "openai",
        "expected_revision": None,
    })["document"]["provider"] == "openai"

    document = {"path": "/sources", "revision": VERSION, "editable": True,
                "pdk_data": "/pdk"}
    monkeypatch.setattr(agent_data, "save", lambda *_args, **_kwargs: document)
    monkeypatch.setattr(agent_settings, "effective_data_sources",
                        lambda *_args, **_kwargs: {})
    result = agent_settings.save_data_sources(hub, {
        "address": None, "pdk_data": "/pdk", "circuit_templates": None,
        "expected_revision": None,
    })
    assert result["document"]["project"] == {
        "pdk_data": "/pdk", "circuit_templates": None}
