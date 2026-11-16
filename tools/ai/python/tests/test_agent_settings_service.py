"""Settings cross the real authenticated service without creating model/EDA sessions."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from agent_service_business_fixtures import business_service, open_session
from sico.agent_resources import data_sources, store
from sico.agent_resources.schema import upgrade_manifest
from sico.codex.backend import CodexBackend
from sico.core.tools import ToolRegistry
from sico.service.remote_frontend import RemoteFrontend
from sico.storage.journal import SessionJournal
from sico.transport.framing import ProtocolError
from test_agent_codex_native import CONTEXT
from test_agent_resource_store import config
from test_codex_resources_runtime import make_resources


@contextmanager
def client(service, project):
    api = RemoteFrontend(service.descriptor, project, "")
    try:
        yield api
    finally:
        api.close_desktop()
        for lane in (api._queries, api._commands, api._events, api._attach):
            lane.thread.join(5)


def test_home_settings_read_save_and_stale_write_without_execution(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        first = api.agent_settings.read().result(5)
        assert first["session"] is None and first["project_defaults"]["revision"] is None
        assert first["selected_defaults"]["source"] == "project"
        saved = api.agent_settings.save(config(tmp_path), None).result(5)
        assert saved["outcome"] == "saved"
        conflict = api.agent_settings.save(config(tmp_path, "other"), None).result(5)
        assert conflict["outcome"] == "conflict"
        assert store.read_project(tmp_path)["manifest"]["skill_roots"][0]["id"] == "docs"
        assert not service.owner.controllers and not service.provider.seen


def test_agent_mode_defaults_to_design_and_saves_for_new_sessions(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        value = api.agent_settings.read().result(5)
        workflow = value["pdk_workflow"]
        assert workflow["status"] == "available"
        assert workflow["pdk_workflow"] == "design"
        saved = api.agent_settings.save_mode("generation", workflow["revision"]).result(5)
        assert saved["outcome"] == "saved"
        value = api.agent_settings.read().result(5)
        assert value["pdk_workflow"]["pdk_workflow"] == "generation"
        assert not service.owner.controllers and not service.provider.seen


def test_agent_model_defaults_to_codex_and_saves_python_for_new_sessions(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        value = api.agent_settings.read().result(5)
        settings = value["model_settings"]
        assert (settings["backend"], settings["provider"]) == ("codex", "responses")
        saved = api.agent_settings.save_model(
            "python", "anthropic", settings["revision"]).result(5)
        assert saved["outcome"] == "saved"
        value = api.agent_settings.read().result(5)
        assert value["model_settings"]["backend"] == "python"
        assert value["model_settings"]["provider"] == "anthropic"
        assert not service.owner.controllers and not service.provider.seen


def test_data_sources_default_save_and_stale_write(tmp_path, monkeypatch):
    pdk, templates = tmp_path / "pdk", tmp_path / "templates"
    pdk.mkdir()
    templates.mkdir()
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        value = api.agent_settings.read().result(5)
        settings = value["data_sources"]
        assert settings["status"] == "available"
        assert settings["revision"] is None
        assert settings["project"] == {"pdk_data": None, "circuit_templates": None}
        saved = api.agent_settings.save_data_sources(
            str(pdk), str(templates), settings["revision"]).result(5)
        assert saved["outcome"] == "saved"
        assert saved["document"]["project"] == {
            "pdk_data": str(pdk), "circuit_templates": str(templates)}
        value = api.agent_settings.read().result(5)
        assert value["data_sources"]["project"]["pdk_data"] == str(pdk)
        conflict = api.agent_settings.save_data_sources(
            str(pdk), None, None).result(5)
        assert conflict["outcome"] == "conflict"
        assert not service.owner.controllers and not service.provider.seen


def test_data_sources_environment_defaults_and_session_capture(tmp_path, monkeypatch):
    pdk, templates = tmp_path / "pdk", tmp_path / "templates"
    pdk.mkdir()
    templates.mkdir()
    with business_service(tmp_path, monkeypatch) as service:
        service.owner._default_environment = {
            "SICO_PDK_DATA": str(pdk),
            "SICO_CIRCUIT_TEMPLATES_DIR": str(templates),
        }
        captured = service.owner._capture_environment(None)
        assert captured["SICO_PDK_DATA"] == str(pdk)
        assert captured["SICO_CIRCUIT_TEMPLATES_DIR"] == str(templates)


def test_new_sessions_capture_project_data_sources(tmp_path, monkeypatch):
    pdk, templates = tmp_path / "pdk", tmp_path / "templates"
    pdk.mkdir()
    templates.mkdir()
    data_sources.save(tmp_path, str(pdk), str(templates), expected_revision=None)
    with business_service(tmp_path, monkeypatch) as service:
        captured = service.owner._capture_environment(None)
        assert captured["SICO_PDK_DATA"] == str(pdk)
        assert captured["SICO_CIRCUIT_TEMPLATES_DIR"] == str(templates)


def test_history_reads_data_sources_from_the_session_snapshot(tmp_path, monkeypatch):
    from sico.core.contracts import BoundContext
    from sico.storage import session_snapshot

    pdk, templates = tmp_path / "pdk", tmp_path / "templates"
    pdk.mkdir()
    templates.mkdir()
    with SessionJournal(tmp_path, "history") as journal:
        row = session_snapshot.capture(
            "history", BoundContext("instance", "generation", "target", {}), None,
            {"SICO_PDK_DATA": str(pdk), "SICO_CIRCUIT_TEMPLATES_DIR": str(templates)},
            {})
        session_snapshot.save(journal.directory, row)
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        value = api.agent_settings.read("history").result(5)
        session = value["data_sources"]["session"]
        assert session["status"] == "available"
        assert session["values"]["pdk_data"]["path"] == str(pdk)
        assert session["values"]["circuit_templates"]["path"] == str(templates)
        assert not service.owner.controllers and not service.provider.seen


def test_new_sessions_capture_the_project_model_settings(tmp_path, monkeypatch):
    from sico.agent_resources import model

    with business_service(tmp_path, monkeypatch) as service:
        model.save(tmp_path, "python", "openai", expected_revision=None)
        captured = service.owner._capture_environment(None)
        assert captured["SICO_BACKEND"] == "python"
        assert captured["SICO_PROVIDER"] == "openai"


def test_provider_precedes_environment_without_model_credentials(tmp_path, monkeypatch):
    external = tmp_path / "provider-resources.json"
    external.write_text(json.dumps(config(tmp_path)))
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        service.owner._default_config = {"external_resources": str(external)}
        service.owner._default_environment = {
            "SICO_EXTERNAL_RESOURCES": str(tmp_path / "missing.json")}
        value = api.agent_settings.read().result(5)
        assert value["defaults_scope"] == "service"
        assert value["selected_defaults"]["source"] == "provider"
        assert value["selected_defaults"]["manifest"]["skill_roots"][0]["enabled"]
        service.owner._default_config = {}
        value = api.agent_settings.read().result(5)
        assert value["selected_defaults"]["status"] == "unavailable"
        assert value["project_defaults"]["editable"]
        assert not service.owner.controllers and not service.provider.seen


def test_unreadable_project_is_not_replaced_by_a_save(tmp_path, monkeypatch):
    document = store.save_project(tmp_path, config(tmp_path), expected_revision=None)
    from pathlib import Path

    path = Path(document["path"])
    path.write_text("invalid resource document")
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        value = api.agent_settings.read().result(5)
        assert value["project_defaults"]["status"] == "unavailable"
        assert "manifest" not in value["project_defaults"]
        outcome = api.agent_settings.save({"version": 2}, document["revision"]).result(5)
        assert outcome["outcome"] in {"rejected", "conflict"}
        assert path.read_text() == "invalid resource document"


def test_external_override_is_visible_and_save_only_updates_project(tmp_path, monkeypatch):
    path, manifest = make_resources(tmp_path)
    original = path.read_bytes()
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        service.owner._default_environment = {"SICO_EXTERNAL_RESOURCES": str(path),
                                               "UNRELATED_SECRET": "DO_NOT_EXPOSE"}
        value = api.agent_settings.read().result(5)
        assert value["selected_defaults"]["source"] == "environment"
        assert not value["selected_defaults"]["editable"]
        assert "DO_NOT_EXPOSE" not in json.dumps(value)
        assert api.agent_settings.save({"version": 2}, None).result(5)["outcome"] == "saved"
        assert path.read_bytes() == original
        value = api.agent_settings.read().result(5)
        expected = {"version": 1, **{k: manifest[k] for k in (
            "skill_roots", "plugins", "capability_roots")}}
        assert value["selected_defaults"]["manifest"] == upgrade_manifest(expected)
        assert not service.owner.controllers


def test_invalid_provider_does_not_prevent_editing_project_defaults(tmp_path, monkeypatch):
    default = tmp_path / ".sico/ai/agent-provider.json"
    store.save_project(tmp_path, {"version": 2}, expected_revision=None)
    default.write_text("invalid provider")
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        value = api.agent_settings.read().result(5)
        assert value["selected_defaults"]["status"] == "unavailable"
        assert value["project_defaults"]["editable"]
        revision = value["project_defaults"]["revision"]
        assert api.agent_settings.save(config(tmp_path), revision).result(5)["outcome"] == "saved"


@pytest.mark.parametrize("kind", ["unsupported", "unknown_field", "revision"])
def test_invalid_input_preserves_defaults(tmp_path, monkeypatch, kind):
    saved = store.save_project(tmp_path, config(tmp_path), expected_revision=None)
    before = store.read_project(tmp_path)
    value, revision = {"version": 2}, saved["revision"]
    if kind == "unsupported":
        from test_agent_resource_schema import extensions

        value["knowledge_sources"] = extensions()["knowledge_sources"]
    elif kind == "unknown_field":
        value["secret"] = "not allowed"
    else:
        revision = False
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        result = api.agent_settings.save(value, revision).result(5)
        assert result["outcome"] == "rejected"
        assert store.read_project(tmp_path) == before


@pytest.mark.parametrize("fault", ["write", "uncertain"])
def test_disk_outcome_is_explicit_and_does_not_echo_secrets(tmp_path, monkeypatch, fault):
    def fail(*_args, **_kwargs):
        error = store.ResourceSaveUncertainError if fault == "uncertain" else OSError
        raise error("secret credentials")
    monkeypatch.setattr(store, "save_project", fail)
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        result = api.agent_settings.save({"version": 2}, None).result(5)
        assert result["outcome"] == "uncertain"
        assert "secret" not in json.dumps(result)


def test_stale_session_scope_and_arbitrary_target_are_rejected(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        address = open_session(service)
        row = address.record()
        row["runtime_id"] = "a" * 32
        with pytest.raises(ValueError, match="运行实例"):
            api._commands.request("agent_settings_save", dict(
                address=row, manifest={"version": 2}, expected_revision=None)).result(5)
        with pytest.raises(ProtocolError):
            api._commands.request("agent_settings_save", dict(
                address=None, manifest={"version": 2}, expected_revision=None,
                path="/tmp/unrelated")).result(5)
        assert store.read_project(tmp_path)["revision"] is None


def test_live_busy_settings_reads_published_snapshot_without_runtime(tmp_path, monkeypatch):
    # The default fixture constructs Python, whose resource capability is
    # deliberately unsupported. Select the real Codex backend at construction
    # so controller delegates and published views share that same owner.
    from sico.codex.settings import CodexSettings

    settings = CodexSettings(
        {"backend": "codex", "provider": "responses",
         "base_url": "http://127.0.0.1:1/v1", "model": "test",
         "api_key_env": "SICO_API_KEY"},
        {"SICO_API_KEY": "controlled"},
    )
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        monkeypatch.setattr("sico.service.project_sessions.provider_from_config",
                            lambda *_args, **_kwargs: settings)
        address = open_session(service)
        controller = service.owner.controllers["live"]
        loop = controller.loop
        loop.resources.prepare()
        controller._running = True
        try:
            row = api._queries.request("agent_settings", {"address": address.record()}).result(5)
            assert row["session"]["snapshot"]["busy"]
            assert row["session"]["snapshot"]["configuration"]["state"] == "frozen"
            assert loop.runtime is None and not service.provider.seen
        finally:
            controller._running = False


def test_history_choice_is_read_without_opening_backend(tmp_path, monkeypatch):
    provider = SimpleNamespace(options={}, environment={})
    with SessionJournal(tmp_path, "history") as journal:
        loop = CodexBackend(provider, ToolRegistry(), journal, CONTEXT)
        loop.resources.prepare()
    store.save_project(tmp_path, config(tmp_path), expected_revision=None)
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        value = api.agent_settings.read("history").result(5)
        assert value["session"]["recorded"]
        effective = value["session"]["snapshot"]["configuration"]["effective"]
        assert effective["manifest"] == upgrade_manifest({"version": 1})
        assert not service.owner.controllers and not service.provider.seen


def test_feature_settings_are_codex_only(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        open_session(service)
        token = api.open_session("live").result(5)
        api.control.ensure(token).result(5)
        with pytest.raises(ValueError, match="不支持 feature_settings"):
            api.command(token, "feature_settings").result(5)


def test_feature_defaults_are_project_scoped(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, client(service, tmp_path) as api:
        read = api.feature_defaults.read().result(5)
        assert read["overrides"] == {}
        saved = api.feature_defaults.save(
            {"view_image": {"features.view_image": False}}).result(5)
        assert saved["overrides"] == {"view_image": {"features.view_image": False}}
        path = tmp_path / ".sico/ai/feature-groups.json"
        assert path.is_file()
        assert api.feature_defaults.read().result(5)["overrides"] == {
            "view_image": {"features.view_image": False}}
        assert not service.owner.controllers
