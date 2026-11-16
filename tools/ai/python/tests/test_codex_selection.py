"""One pinned core, optional tool wrapper, strict failures and startup isolation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cadai.codex_selection import (
    CodexConfigurationError,
    codex_tool_format,
    require_supported_runtime,
    verify_codex_version,
)
from cadai.launch import prepare_codex_gateway, private_codex_home, resolve_agent_executable
from sico.codex.settings import CodexSettings
from sico.providers.config import load_provider, provider_from_config

from assistant_installation_fixture import assistant_installation


@pytest.fixture(autouse=True)
def isolated_installation(tmp_path, monkeypatch):
    assistant_installation(tmp_path, monkeypatch)


def executable(path: Path, version="0.162.1") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nprintf "codex-cli ' + version + '\\n"\n', encoding="ascii")
    path.chmod(0o755)
    return path


def provider_environment(**options):
    return {
        "SICO_API_URL": "https://gateway.example/v1",
        "SICO_MODEL": "test-model",
        "SICO_API_KEY": "test-secret",
        **options,
    }


@pytest.mark.parametrize("value", [None, "", "0", "1"])
def test_supported_selection(value):
    environment = {} if value is None else {"SICO_LEGACY_0114": value}
    assert require_supported_runtime(environment) is None


@pytest.mark.parametrize("value", ["true", "yes", "0114", "secret-value"])
def test_invalid_switch_does_not_reflect_input(value):
    with pytest.raises(CodexConfigurationError) as error:
        load_provider(environment=provider_environment(SICO_LEGACY_0114=value))
    assert "it selects a tool wrapper" in str(error.value)
    assert "secret-value" not in str(error.value)


@pytest.mark.parametrize("switch", [None, "0", "1"])
def test_only_current_executable_starts(tmp_path, switch):
    root = tmp_path / "tools/ai"
    bundled = executable(root / "runtime/codex/x86_64-unknown-linux-musl/bin/codex")
    old = executable(tmp_path / "old/codex", "0.114.0")
    environment = {"CAD_CODEX_LEGACY_CLI": str(old)}
    if switch is not None:
        environment["SICO_LEGACY_0114"] = switch
    old.write_text('#!/bin/sh\nprintf "unused executable started" >&2\nexit 1\n')
    assert resolve_agent_executable(
        "codex", environment=environment, install_root=root
    ) == str(bundled)


def test_compatibility_switch_still_requires_0154_executable(tmp_path):
    root = tmp_path / "tools/ai"
    executable(root / "runtime/codex/x86_64-unknown-linux-musl/bin/codex")
    old = executable(tmp_path / "old/codex", "0.114.0")
    with pytest.raises(CodexConfigurationError, match="0.162.1"):
        resolve_agent_executable(
            "codex", str(old), environment={"SICO_LEGACY_0114": "1"}, install_root=root
        )


@pytest.mark.parametrize("version", ["0.114.0", "0.153.4", "0.155.0"])
def test_wrong_version_never_falls_back(tmp_path, version):
    root = tmp_path / "tools/ai"
    executable(root / "runtime/codex/x86_64-unknown-linux-musl/bin/codex")
    wrong = executable(tmp_path / "wrong/codex", version)
    with pytest.raises(CodexConfigurationError, match="must report"):
        resolve_agent_executable(
            "codex", environment={
                "SICO_CODEX_CLI": str(wrong),
            }, install_root=root,
        )


def test_missing_override_does_not_fall_back_to_bundle(tmp_path):
    root = tmp_path / "tools/ai"
    executable(root / "runtime/codex/x86_64-unknown-linux-musl/bin/codex")
    with pytest.raises(CodexConfigurationError, match="not found"):
        resolve_agent_executable(
            "codex", str(tmp_path / "missing"), environment={}, install_root=root
        )


def test_path_lookup_uses_supplied_environment(tmp_path, monkeypatch):
    wrong = executable(tmp_path / "wrong/codex", "0.114.0")
    current = executable(tmp_path / "current/codex")
    monkeypatch.setenv("PATH", str(wrong.parent))
    assert resolve_agent_executable(
        "codex", "codex", environment={"PATH": str(current.parent)}
    ) == str(current)


def test_version_probe_excludes_all_model_keys_and_unlisted_environment(tmp_path):
    script = executable(tmp_path / "codex")
    script.write_text(
        '#!/bin/sh\n[ -z "${SICO_API_KEY}${CAD_CODEX_API_KEY}${CUSTOM_MODEL_KEY}'
        '${CAD_COPILOT_MODEL_KEY}${UNLISTED_HOST_VALUE}" ] || exit 1\n'
        'printf "codex-cli 0.162.1\\n"\n'
    )
    environment = dict.fromkeys(
        ("SICO_API_KEY", "SICO_CODEX_API_KEY", "CUSTOM_MODEL_KEY",
         "CAD_COPILOT_MODEL_KEY", "UNLISTED_HOST_VALUE"), "probe-secret",
    )
    verify_codex_version(str(script), environment)


@pytest.mark.parametrize("failure", ["output", "timeout", "oserror"])
def test_version_probe_errors_are_credential_free(monkeypatch, failure):
    def probe(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(["private-value"], 10, stderr="private-value")
        if failure == "oserror":
            raise OSError("private-value")
        return subprocess.CompletedProcess(args, 1, b"private-value", b"private-value")

    monkeypatch.setattr("cadai.codex_selection.subprocess.run", probe)
    with pytest.raises(CodexConfigurationError) as error:
        verify_codex_version("/private-value", {})
    assert "private-value" not in str(error.value)


def test_wrapper_switch_keeps_same_core_home_and_preserves_historical_data(tmp_path, monkeypatch):
    monkeypatch.setenv("SICO_CODEX_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SICO_LEGACY_0114", raising=False)
    current = private_codex_home()
    (current / "config.toml").write_text('model_reasoning_effort="max"\n')
    old = current / "legacy-0.114.0"
    old.mkdir(mode=0o700)
    (old / "history.jsonl").write_text('{"historical":true}\n')
    monkeypatch.setenv("SICO_LEGACY_0114", "1")
    assert private_codex_home() == current
    monkeypatch.delenv("SICO_LEGACY_0114")
    assert private_codex_home() == current
    assert (current / "config.toml").read_text() == 'model_reasoning_effort="max"\n'
    assert (old / "history.jsonl").read_text() == '{"historical":true}\n'


@pytest.mark.parametrize("backend", [None, "", "codex"])
def test_copilot_defaults_to_codex_responses(backend):
    environment = provider_environment()
    if backend is not None:
        environment["SICO_BACKEND"] = backend
    provider = load_provider(environment=environment)
    assert isinstance(provider, CodexSettings)
    assert provider.endpoint == "https://gateway.example/v1/responses"
    assert provider.label.startswith("Codex 0.162.1 /")
    assert provider.options["provider"] == "responses"
    assert "codex_executable" not in provider.options
    assert "test-secret" not in json.dumps(provider.options)


def test_copilot_responses_json_needs_no_backend_field(tmp_path):
    path = tmp_path / "provider.json"
    path.write_text(json.dumps({
        "provider": "responses", "base_url": "https://gateway.example/v1", "model": "model",
    }))
    provider = load_provider(path, environment={"SICO_API_KEY": "secret"})
    assert isinstance(provider, CodexSettings)


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
def test_old_environment_protocol_requires_explicit_python_backend(protocol):
    environment = provider_environment(SICO_PROVIDER=protocol)
    with pytest.raises(ValueError, match="SICO_PROVIDER"):
        load_provider(environment=environment)
    environment["SICO_BACKEND"] = "python"
    assert not isinstance(load_provider(environment=environment), CodexSettings)


def test_copilot_selects_wrapper_for_environment_and_explicit_json(tmp_path):
    environment = provider_environment(SICO_LEGACY_0114="1")
    path = tmp_path / "provider.json"
    path.write_text(json.dumps({
        "backend": "codex", "provider": "responses", "model": "test",
        "base_url": "https://gateway.example/v1",
    }))
    for selected in (None, path):
        provider = load_provider(selected, environment=environment)
        assert isinstance(provider, CodexSettings) and provider.tool_format == "flat"
        assert provider.label.startswith("Codex 0.162.1")
    assert not isinstance(provider_from_config(None, environment=environment), CodexSettings)


def test_copilot_uses_shared_bundle_and_keeps_startup_environment(tmp_path, monkeypatch):
    root = tmp_path / "tools/ai"
    bundled = executable(root / "runtime/codex/x86_64-unknown-linux-musl/bin/codex")
    environment = provider_environment(
        SICO_AI_RELEASE_ROOT=str(root), CAD_CODEX_CLI="/unrelated/terminal-override",
        CAD_CODEX_LEGACY_CLI="/unrelated/legacy-override",
    )
    provider = load_provider(environment=environment)
    environment["SICO_LEGACY_0114"] = "1"
    monkeypatch.setenv("SICO_LEGACY_0114", "1")
    assert provider.executable() == str(bundled)
    assert provider.tool_format == "native"


@pytest.mark.parametrize(
    "switch,configured,expected",
    [(None, None, "native"), (None, "flat", "flat"), (None, "native", "native"),
     ("1", "native", "flat"), ("0", "flat", "native"), ("", "flat", "flat")],
)
def test_wrapper_switch_precedence(switch, configured, expected):
    environment = {} if switch is None else {"SICO_LEGACY_0114": switch}
    assert codex_tool_format(environment, configured) == expected
    options = provider_environment(**environment)
    if configured is not None:
        options["SICO_TOOL_FORMAT"] = configured
    assert load_provider(environment=options).tool_format == expected


def test_native_terminal_does_not_create_wrapper(monkeypatch):
    monkeypatch.setattr(
        "cadai.responses_wrapper.ResponsesWrapper",
        lambda *a, **kw: pytest.fail("Native mode must not construct a wrapper"),
    )
    assert prepare_codex_gateway({}) is None


def test_terminal_wrapper_owns_key_and_closes_listener():
    import socket
    from urllib.parse import urlsplit

    environment = {
        "SICO_LEGACY_0114": "1", "SICO_API_BASE_URL": "https://gateway.example/v1",
        "SICO_API_KEY": "gateway-secret",
        "OPENAI_API_KEY": "gateway-secret",
    }
    wrapper = prepare_codex_gateway(environment)
    try:
        assert wrapper.api_key == "gateway-secret"
        assert "gateway-secret" not in json.dumps(environment)
        assert environment["SICO_API_KEY"] == wrapper.token
        port = urlsplit(environment["SICO_API_BASE_URL"]).port
    finally:
        wrapper.close()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1)


def test_terminal_wrapper_rejects_implicit_provider_without_starting():
    with pytest.raises(CodexConfigurationError, match="SICO_API_BASE_URL"):
        prepare_codex_gateway({"SICO_LEGACY_0114": "1"})


def test_retired_cad_agent_tool_env_names_require_migration():
    for setting in ({"CAD_AGENT_TOOL_FORMAT": "flat"}, {"CAD_AGENT_LEGACY_0114": "1"}):
        with pytest.raises(ValueError, match="was removed"):
            codex_tool_format(setting)
    mixed = {"SICO_TOOL_FORMAT": "native", "CAD_AGENT_TOOL_FORMAT": "flat"}
    assert codex_tool_format(mixed) == "native"
