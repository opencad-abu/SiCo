"""Structured data roots keep project overrides separate from site defaults."""

import json

import pytest
from sico.agent_resources import data_sources

from cadai.optional_resources import (
    DATA_SOURCE_ENV,
    apply_data_sources,
    effective_data_sources,
)


def roots(tmp_path):
    pdk, templates = tmp_path / "pdk", tmp_path / "templates"
    pdk.mkdir()
    templates.mkdir()
    return pdk, templates


def test_defaults_save_clear_and_revision_round_trip(tmp_path):
    pdk, templates = roots(tmp_path)
    initial = data_sources.read(tmp_path)
    assert initial["revision"] is None and initial["editable"]
    assert initial.get("pdk_data") is None
    assert initial.get("circuit_templates") is None

    saved = data_sources.save(
        tmp_path, str(pdk), str(templates), expected_revision=None)
    assert saved["pdk_data"] == str(pdk)
    assert saved["circuit_templates"] == str(templates)
    assert data_sources.read(tmp_path)["revision"] == saved["revision"]

    cleared = data_sources.save(
        tmp_path, None, None, expected_revision=saved["revision"])
    assert "pdk_data" not in cleared and "circuit_templates" not in cleared
    assert data_sources.read(tmp_path)["revision"] == cleared["revision"]


def test_stale_revision_and_invalid_paths_are_rejected(tmp_path):
    pdk, templates = roots(tmp_path)
    data_sources.save(tmp_path, str(pdk), str(templates), expected_revision=None)
    with pytest.raises(data_sources.DataSourcesConflictError):
        data_sources.save(tmp_path, str(pdk), None, expected_revision=None)
    with pytest.raises(ValueError, match="绝对路径"):
        data_sources.save(tmp_path, "relative/path", None, expected_revision=None)
    with pytest.raises(ValueError, match="绝对路径"):
        data_sources.save(tmp_path, "/bad\npath", None, expected_revision=None)


def test_invalid_document_is_reported_without_replacing_it(tmp_path):
    from sico.storage.roots import state_root

    path = state_root(tmp_path, create=True) / "ai/data-sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "secret": "/tmp/x"}))
    with pytest.raises(ValueError, match="格式无效"):
        data_sources.read(tmp_path)
    assert "secret" in path.read_text()


def test_effective_project_override_wins_and_clears_to_environment(tmp_path):
    project_pdk, project_templates = roots(tmp_path)
    env_pdk, env_templates = tmp_path / "env-pdk", tmp_path / "env-templates"
    env_pdk.mkdir()
    env_templates.mkdir()
    environment = {
        "SICO_PDK_DATA": str(env_pdk),
        "SICO_CIRCUIT_TEMPLATES_DIR": str(env_templates),
    }
    data_sources.save(
        tmp_path, str(project_pdk), None, expected_revision=None)
    values = effective_data_sources(environment, tmp_path)
    assert values["pdk_data"]["source"] == "project"
    assert values["pdk_data"]["path"] == str(project_pdk)
    assert values["circuit_templates"]["source"] == "environment"
    assert values["circuit_templates"]["path"] == str(env_templates)

    current = data_sources.read(tmp_path)
    data_sources.save(tmp_path, None, None, expected_revision=current["revision"])
    values = effective_data_sources(environment, tmp_path)
    assert values["pdk_data"]["source"] == "environment"
    assert values["pdk_data"]["path"] == str(env_pdk)


def test_relative_environment_paths_bind_to_the_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(tmp_path)
    values = effective_data_sources(
        {"SICO_CIRCUIT_TEMPLATES_DIR": "data/templates"}, workspace)
    assert values["circuit_templates"]["path"] == str(workspace / "data/templates")


def test_apply_sets_only_effective_roots(tmp_path):
    pdk, templates = roots(tmp_path)
    env = {"SICO_PDK_DATA": str(pdk), "SICO_CIRCUIT_TEMPLATES_DIR": str(templates)}
    data_sources.save(tmp_path, None, str(templates), expected_revision=None)
    applied = {}
    values = apply_data_sources(applied, tmp_path, source=env)
    assert applied["SICO_PDK_DATA"] == str(pdk)
    assert applied["SICO_CIRCUIT_TEMPLATES_DIR"] == str(templates)
    assert values["pdk_data"]["source"] == "environment"
    assert values["circuit_templates"]["source"] == "project"

    empty = {}
    apply_data_sources(empty, tmp_path / "none", source={})
    assert not set(DATA_SOURCE_ENV.values()) & set(empty)


def test_availability_distinguishes_missing_and_wrong_type(tmp_path):
    file_path = tmp_path / "not-a-directory"
    file_path.write_text("x")
    values = effective_data_sources(
        {"SICO_CIRCUIT_TEMPLATES_DIR": str(file_path)}, tmp_path)
    assert values["circuit_templates"]["status"] == "invalid_type"
    values = effective_data_sources(
        {"SICO_PDK_DATA": str(tmp_path / "missing")}, tmp_path)
    assert values["pdk_data"]["status"] == "missing"
