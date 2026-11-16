from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from sico.providers.config import environment_config

from cadai.codex_launch import prepare_codex_gateway
from cadai.launch import build_codex_command


def _command(environment: Mapping[str, str] | None = None) -> list[str]:
    return build_codex_command(
        "/bin/codex",
        "/bin/python3",
        Path("/work"),
        Path("/cad/python"),
        300,
        environment=environment,
    )


def test_codex_command_defers_optional_reference_validation() -> None:
    assert _command({"SICO_AI_SKILL_REFERENCE_ROOT": "relative/reference"})


def test_model_and_responses_api_environment() -> None:
    api_key = "secret-that-must-not-appear-in-argv"
    command = _command(
        {
            "SICO_MODEL": 'proxy model "quoted"',
            "SICO_API_BASE_URL": 'https://gateway.example/a%20path/"quoted"/v1',
            "SICO_API_KEY": api_key,
        }
    )
    joined = " ".join(command)
    assert command[-6:-4] == ["-m", 'proxy model "quoted"']
    assert "model_providers.sico-environment=" in joined
    assert 'base_url="https://gateway.example/a%20path/\\"quoted\\"/v1"' in joined
    assert 'env_key="SICO_API_KEY"' in joined
    assert 'model_provider="sico-environment"' in joined
    assert api_key not in joined
    option = next(value for value in command if value.startswith("mcp_servers.sico_ic.env_vars="))
    inherited = json.loads(option.split("=", 1)[1])
    assert {
        "SICO_AI_RUNTIME", "SICO_AI_SOCKET", "SICO_AI_SPOOL", "SICO_AI_TOKEN", "SICO_AI_WORKSPACE",
        "SICO_PYTHON", "CAD_TEMP_DIR", "TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR",
        "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
    } <= set(inherited)
    assert not {"SICO_API_KEY", "SICO_API_KEY", "OPENAI_API_KEY"} & set(inherited)


def test_model_override_does_not_change_provider() -> None:
    command = _command({"SICO_MODEL": "openai-model"})
    model_index = command.index("-m")
    assert command[model_index : model_index + 2] == ["-m", "openai-model"]
    assert not any("model_provider=" in item for item in command)


def test_process_model_environment_is_read(monkeypatch) -> None:
    monkeypatch.setenv("SICO_MODEL", "process-environment-model")
    command = _command()
    model_index = command.index("-m")
    assert command[model_index : model_index + 2] == [
        "-m",
        "process-environment-model",
    ]


def test_blank_environment_is_ignored() -> None:
    baseline = _command({})
    configured = _command(
        {
            "SICO_MODEL": "  ",
            "SICO_API_BASE_URL": "\t",
            "SICO_API_KEY": "\n",
        }
    )
    assert configured == baseline


def test_api_key_without_base_url_uses_openai_api() -> None:
    joined = " ".join(_command({"SICO_API_KEY": "secret"}))
    assert 'base_url="https://api.openai.com/v1"' in joined
    assert "secret" not in joined


def test_unauthenticated_base_url_does_not_require_key() -> None:
    joined = " ".join(_command({"SICO_API_BASE_URL": "http://127.0.0.1:11434/v1"}))
    assert 'env_key="SICO_API_KEY"' not in joined
    assert 'model_provider="sico-environment"' in joined


@pytest.mark.parametrize("url_name", ["SICO_API_BASE_URL", "SICO_API_URL"])
def test_assistant_and_service_share_model_identity(url_name):
    environment = {url_name: "https://proxy.example/v1", "SICO_MODEL": "doubao",
                   "SICO_API_KEY": "private-secret"}
    command = _command(environment)
    config = environment_config(environment)
    assert command[command.index("-m") + 1] == config["model"] == "doubao"
    assert config["base_url"] in " ".join(command)
    assert config["api_key_env"] == "SICO_API_KEY"
    assert "private-secret" not in " ".join(command)


def test_doubao_terminal_does_not_request_reasoning_summary() -> None:
    command = _command({
        "SICO_API_BASE_URL": "https://proxy.example/v1",
        "SICO_MODEL": "doubao",
        "SICO_API_KEY": "private-secret",
    })
    assert 'model_reasoning_summary="none"' in command
    plain = _command({
        "SICO_API_BASE_URL": "https://proxy.example/v1",
        "SICO_MODEL": "deepseek-v4-flash",
        "SICO_API_KEY": "private-secret",
    })
    assert 'model_reasoning_summary="none"' not in plain


def test_shared_settings_ignore_and_remove_stale_codex_identity():
    environment = {"SICO_API_BASE_URL": "https://proxy.example/v1", "SICO_MODEL": "shared",
                   "SICO_API_KEY": "shared-key", "SICO_LEGACY_0114": "1",
                   "SICO_CODEX_MODEL": "stale", "SICO_CODEX_API_BASE_URL": "https://old.example",
                   "SICO_CODEX_API_KEY": "old-key"}
    wrapper = prepare_codex_gateway(environment)
    try:
        assert wrapper.api_key == "shared-key"
        assert wrapper.url.hostname == "proxy.example"
        assert environment["SICO_API_KEY"] == wrapper.token
        assert environment["SICO_API_BASE_URL"] == wrapper.base_url
        assert not any(name.startswith("SICO_CODEX_") for name in environment)
        assert "shared-key" not in str(environment) and "old-key" not in str(environment)
        assert _command(environment)[-1]  # Shared wrapper identity is consumable.
    finally:
        wrapper.close()


def test_conflicting_shared_endpoints_never_choose_silently():
    environment = {"SICO_API_BASE_URL": "https://new.example/v1",
                   "SICO_API_URL": "https://old.example/v1", "SICO_MODEL": "model"}
    for reader in (_command, environment_config):
        with pytest.raises(ValueError, match="conflicts") as exc:
            reader(environment)
        assert "https://" not in str(exc.value)


def test_codex_only_configuration_requests_shared_variables():
    with pytest.raises(ValueError, match="Use shared SICO_API_BASE_URL"):
        _command({"SICO_CODEX_MODEL": "old-model"})
