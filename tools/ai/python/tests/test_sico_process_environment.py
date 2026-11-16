"""Process handoff uses current names and rejects ambiguous installation roots."""

import warnings
from pathlib import Path

import pytest

from cadai.codex_launch import prepare_codex_gateway
from cadai.installation import runtime_root
from cadai.launch_executable import production_python, resolve_agent_executable
from sico.codex.settings import codex_tool_timeout
from sico.core.contracts import BoundContext
from sico.storage.session_snapshot import capture, fingerprint, validate
from sicoenv import RETIRED_NAMES, promote, read

from assistant_installation_fixture import assistant_installation


@pytest.mark.parametrize("current,old", [
    (current, old) for current, aliases in RETIRED_NAMES.items() for old in aliases])
def test_current_empty_is_authoritative_and_handoff_drops_old_names(current, old):
    env = {current: "", old: "private-conflicting-value"}
    promote(env, current)
    assert env == {current: ""}
    with pytest.raises(ValueError, match="was removed") as result:
        read({old: "private-conflicting-value"}, current)
    assert "private-conflicting-value" not in str(result.value)


@pytest.mark.parametrize("name", ["SICO_PYTHON", "SICO_PYTHON_ROOT"])
def test_empty_new_python_setting_cannot_use_legacy_or_default(name):
    with pytest.raises(RuntimeError, match="must not be empty"):
        production_python({name: "", RETIRED_NAMES[name][0]: "/legacy/python"})


def test_empty_new_timeout_cannot_use_old_budget():
    with pytest.raises(ValueError, match="MCP timeout"):
        codex_tool_timeout({"SICO_MCP_TIMEOUT": "", "CAD_AGENT_MCP_TIMEOUT": "50"})


def test_empty_new_gateway_key_cannot_use_old_secret():
    with pytest.raises(ValueError):
        prepare_codex_gateway({"SICO_TOOL_FORMAT": "flat", "SICO_CODEX_API_KEY": "",
                              "CAD_CODEX_API_KEY": "secret", "SICO_CODEX_API_BASE_URL": "https://example/v1"})


def test_runtime_roots_require_one_installation(tmp_path, monkeypatch):
    root = assistant_installation(tmp_path / "install", monkeypatch)
    tool = root / "tools/ai"
    alias = tmp_path / "site-ai"
    alias.symlink_to(tool, target_is_directory=True)
    with pytest.raises(ValueError, match="was removed"):
        runtime_root({"SICO_HOME": str(root), "CAD_AI_RELEASE_ROOT": str(alias)})
    for configured in ("", str(tmp_path / "other"), "relative"):
        with pytest.raises(ValueError, match="conflicts"):
            runtime_root({"SICO_HOME": str(root), "SICO_AI_RELEASE_ROOT": configured})
    with pytest.raises(ValueError, match="must not be empty"):
        resolve_agent_executable("codex", environment={"SICO_CODEX_CLI": "", "CAD_CODEX_CLI": "/bin/true"})


def test_new_snapshots_promote_but_historical_records_remain_valid():
    context = BoundContext("instance", "generation", "target", {})
    env = {"CAD_PYTHON": "/old/python", "SICO_PYTHON": "/new/python", "SICO_AI_CONTEXT": "/current/runtime.cxt"}
    current = capture("session", context, None, env, {})
    assert current["environment"] == {"SICO_PYTHON": "/new/python", "SICO_AI_CONTEXT": "/current/runtime.cxt"}
    historical = dict(current, environment={"CAD_PYTHON": "/historical/python"})
    historical["revision"] = fingerprint({k:v for k,v in historical.items() if k != "revision"})
    assert validate(historical, "session")["environment"] == {"CAD_PYTHON": "/historical/python"}
