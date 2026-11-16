"""Terminal and MCP process boundaries use the same project state contract."""

import json
import os
from pathlib import Path

import pytest

from cadenv import EDA_TEMP_ENV_NAMES, EDA_TEMP_MARKERS, restore_eda_temp_environment
from cadai.claude_launch import build_claude_mcp_config
from cadai.codex_launch import build_codex_command
from cadai.runtime import RuntimePaths, write_spool
from sico.codex.runtime import mcp_environment_vars
from sico.service.launch import desktop_environment
from sicotemp import initialize


@pytest.mark.parametrize("legacy", [False, True])
def test_shared_bridge_and_local_runtime_have_one_state_owner(tmp_path, monkeypatch, legacy):
    legacy_state = tmp_path / ".cad"
    if legacy:
        legacy_state.mkdir(mode=0o700)
    environment = {}
    initialize(environment, cwd=tmp_path)
    state = tmp_path / ".sico"
    if legacy:
        assert (state / "legacy-ignored.json").is_file()
        assert not list(legacy_state.iterdir())
    for name in ("CAD_TEMP_DIR", "SICO_TEMP_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SICO_TEMP_DIR", environment["SICO_TEMP_DIR"])
    runtime = RuntimePaths.create(spool_base=state / "ai")
    assert runtime.root.parent == state / "ai"
    assert runtime.root.name.startswith("sico-ai-")
    assert runtime.virtuoso_spool.parent.name == f".sico-ai-runtime-{os.getuid()}"
    payload = write_spool(runtime.virtuoso_spool, "probe", b"ok")
    Path(payload["path"]).unlink()
    runtime.cleanup()
    assert not runtime.root.exists() and not runtime.virtuoso_spool.exists()
    if not legacy:
        assert not legacy_state.exists()


def test_claude_mcp_preserves_eda_markers_from_terminal(tmp_path):
    environment = {"TMPDIR": "/vendor/temporary", "TEMP": "", "XDG_CACHE_HOME": "/vendor/cache"}
    initialize(environment, cwd=tmp_path)
    runtime = RuntimePaths.create(runtime_base=tmp_path / ".sico/ai")
    try:
        payload = build_claude_mcp_config("/bin/python3", tmp_path / "mcp", runtime,
                                         "test-token", 5, environment=environment)
        child = json.loads(payload)["mcpServers"]["sico_ic"]["env"]
        assert child["SICO_TEMP_DIR"] == str(tmp_path / ".sico")
        assert "CAD_TEMP_DIR" not in child
        restored = restore_eda_temp_environment(child)
        assert {key: restored[key] for key in EDA_TEMP_ENV_NAMES if key in restored} == {
            "TMPDIR": "/vendor/temporary", "TEMP": "", "XDG_CACHE_HOME": "/vendor/cache"}
    finally:
        runtime.cleanup()


def test_codex_mcp_allowlists_include_original_temp_markers(tmp_path):
    command = build_codex_command("/bin/codex", "/bin/python3", tmp_path, tmp_path, 5,
                                  environment={})
    argument = next(arg for arg in command if arg.startswith("mcp_servers.sico_ic.env_vars="))
    terminal = json.loads(argument.split("=", 1)[1])
    for names in (terminal, mcp_environment_vars({})):
        assert set(EDA_TEMP_MARKERS) <= set(names)
        assert {"SICO_TEMP_DIR", "CAD_TEMP_DIR"} <= set(names)


def test_desktop_keeps_vendor_temp_before_session_scratch(tmp_path):
    source = {"SICO_PYTHON": "/software/pkgs/python/3.9.13/bin/python3",
              "TMPDIR": "/vendor/tmp", "XDG_RUNTIME_DIR": "/vendor/runtime"}
    child = desktop_environment(tmp_path, "probe", source)
    restored = restore_eda_temp_environment(child)
    assert restored["TMPDIR"] == source["TMPDIR"]
    assert restored["XDG_RUNTIME_DIR"] == source["XDG_RUNTIME_DIR"]
    assert "SICO_TEMP_DIR" not in restored
