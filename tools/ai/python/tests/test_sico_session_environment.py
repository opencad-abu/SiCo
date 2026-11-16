"""Delegated session environment aliases remain scoped and empty-safe."""

import os
from types import SimpleNamespace

import pytest

from sicosessionenv import ENVIRONMENT_NAMES, consume, publish, value
from cadai import mcp_environment
from cadai.claude_launch import build_claude_mcp_config
from cadai.pdk_worker import worker_environment


@pytest.mark.parametrize("family", ["SICO_AI_", "CAD_AI_"])
def test_control_pipe_adoption_consumes_aliases(family):
    import os
    from cad_ai_terminal.protocol import parse_control_fd

    reader, writer = os.pipe()
    try:
        environment = {family + "CONTROL_FD": str(reader)}
        if family != "SICO_AI_":
            with pytest.raises(ValueError, match="removed"):
                parse_control_fd(environment)
            os.fstat(reader)
            return
        assert parse_control_fd(environment) == reader
        assert environment == {}
        assert not os.get_blocking(reader) and not os.get_inheritable(reader)
    finally:
        os.close(reader)
        os.close(writer)


def test_empty_new_control_fd_does_not_adopt_legacy_pipe():
    import os
    from cad_ai_terminal.protocol import parse_control_fd

    reader, writer = os.pipe()
    try:
        environment = {"SICO_AI_CONTROL_FD": "", "CAD_AI_CONTROL_FD": str(reader)}
        assert parse_control_fd(environment) is None
        assert environment == {}
        os.fstat(reader)
    finally:
        os.close(reader)
        os.close(writer)


def test_new_empty_token_wins_without_exposing_either_value():
    env = {"SICO_AI_TOKEN": "", "CAD_AI_TOKEN": "secret-legacy"}
    assert consume(env)["TOKEN"] == ""
    assert not env


def test_publish_replaces_stale_delegation_and_preserves_unrelated_settings():
    env = {name: "stale" for name in ENVIRONMENT_NAMES}
    env["SICO_AI_QT_PLUGIN_PATH"] = "/plugins"
    publish(env, RUNTIME="/runtime", SOCKET="/runtime/bridge.sock", TOKEN="new")
    assert env == {"SICO_AI_RUNTIME": "/runtime", "SICO_AI_SOCKET": "/runtime/bridge.sock",
                   "SICO_AI_TOKEN": "new", "SICO_AI_QT_PLUGIN_PATH": "/plugins"}
    assert not set(ENVIRONMENT_NAMES) & worker_environment(env).keys()


@pytest.mark.parametrize("family", ["SICO_AI_", "CAD_AI_", "CAD_CODEX_"])
def test_registered_family_is_read_and_consumed(family):
    env = {family + field: field.lower() for field in ("RUNTIME", "SOCKET", "TOKEN")}
    if family != "SICO_AI_":
        with pytest.raises(ValueError, match="removed"):
            value(env, "TOKEN")
        return
    assert value(env, "TOKEN") == "token"
    assert consume(env)["RUNTIME"] == "runtime"
    assert not env


def test_empty_new_credentials_reject_before_connect_and_remove_legacy(monkeypatch):
    for name in ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SICO_AI_TOKEN", "")
    monkeypatch.setenv("CAD_AI_TOKEN", "legacy-secret")
    monkeypatch.setattr(mcp_environment, "SocketClient", lambda *_: pytest.fail("connected"))
    with pytest.raises(RuntimeError, match="SICO_AI_TOKEN"):
        mcp_environment.run_mcp_from_environment()
    assert not set(ENVIRONMENT_NAMES) & os.environ.keys()


def test_claude_explicit_empty_library_path_suppresses_parent_loader(tmp_path):
    import json
    runtime = SimpleNamespace(root=tmp_path, socket=tmp_path / "bridge.sock", spool=tmp_path / "spool")
    payload = json.loads(build_claude_mcp_config("/bin/python", tmp_path / "mcp", runtime,
        "private-token", 30, environment={"SICO_AI_PYTHON_LIBRARY_PATH": "",
        "LD_LIBRARY_PATH": "/eda/lib"}))
    env = payload["mcpServers"]["sico_ic"]["env"]
    assert env["LD_LIBRARY_PATH"] == ""
    assert env["SICO_AI_TOKEN"] == "private-token" and "CAD_AI_TOKEN" not in env
