"""Client launch policy and controller runtime-directory selection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cadai.controller as controller_module
from cadai.launch import build_codex_command


def test_codex_command_enables_mcp_without_legacy_sandbox_or_config_write() -> None:
    command = build_codex_command(
        "/bin/codex",
        "/bin/python3",
        Path("/work"),
        Path("/cad/python"),
        300,
        environment={},
    )
    joined = " ".join(command)
    assert "approval_mode=" not in joined
    assert "-a on-request" in joined
    assert "env_vars=" in joined
    assert 'trust_level="untrusted"' in joined
    assert 'projects={"/"=' in joined
    assert "--disable plugins --disable apps" in joined
    assert "use_linux_sandbox_bwrap" not in joined
    assert "use_legacy_landlock" not in joined
    assert (
        'shell_environment_policy.exclude=["SICO_AI_*","SICO_CODEX_*","SICO_CLAUDE_*","CAD_AI_*","CAD_CODEX_*","CAD_CLAUDE_*",'
        '"SICO_API_KEY","OPENAI_API_KEY","ANTHROPIC_*","CLAUDE_CODE_*"]'
    ) in joined
    assert '["-s", "/cad/python/sico-ai", "mcp", "--request-timeout", "305"]' in joined
    assert "tool_timeout_sec=310" in joined
    assert "mcp add" not in joined


def test_codex_verification_profile_is_read_only() -> None:
    command = build_codex_command(
        "/bin/codex", "/bin/python3", Path("/work"), Path("/cad/python"), 300,
        environment={}, profile="verification",
    )
    assert command[command.index("-s") + 1] == "read-only"


def test_codex_verification_profile_only_enables_bounded_tools() -> None:
    command = build_codex_command(
        "/bin/codex",
        "/bin/python3",
        Path("/work"),
        Path("/cad/python"),
        300,
        environment={},
        profile="verification",
    )
    enabled = json.loads(
        next(
            item.split("=", 1)[1]
            for item in command
            if item.startswith("mcp_servers.sico_ic.enabled_tools=")
        )
    )
    assert set(enabled) == {
        "get_context",
        "run_aivw_recipe",
        "submit_candidate",
        "get_candidate",
        "inspect_schematic",
        "inspect_config_binding",
        "snapshot_schematic",
        "query_schematic",
        "get_schematic_item",
        "export_schematic_text",
        "list_windows",
        "live_model_status",
        "live_model_events",
        "list_project_documents",
        "read_project_document",
        "search_project_documents",
    }
    joined = " ".join(command)
    assert "tools.eval_skill.approval_mode" not in joined
    assert "tools.inspect_layout.approval_mode" not in joined


@pytest.mark.parametrize("shared_spool", [False, True])
def test_session_selects_launch_temp_as_shared_spool_base(
    tmp_path: Path, monkeypatch, shared_spool: bool, codex_stub: Path
) -> None:
    captured: list[tuple[Path | None, Path | None]] = []

    class StopAfterRuntimeSelection(RuntimeError):
        pass

    def capture_create(spool_base=None, runtime_base=None):
        captured.append((spool_base, runtime_base))
        raise StopAfterRuntimeSelection

    monkeypatch.setattr(controller_module.RuntimePaths, "create", capture_create)
    monkeypatch.delenv("CAD_HOME", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(StopAfterRuntimeSelection):
        controller_module.run_session(
            tmp_path,
            "/bin/true",
            str(codex_stub),
            300,
            shared_spool=shared_spool,
        )

    workspace = tmp_path.resolve()
    assert captured == [
        (workspace / ".sico" / "ai" if shared_spool else None, workspace / ".sico" / "ai")
    ]


def test_session_uses_launch_cwd_when_workspace_differs(
    tmp_path: Path, monkeypatch, codex_stub: Path
) -> None:
    launch = tmp_path / "launch"
    workspace = tmp_path / "workspace"
    launch.mkdir()
    workspace.mkdir()
    captured: list[tuple[Path | None, Path | None]] = []

    class StopAfterRuntimeSelection(RuntimeError):
        pass

    def capture_create(spool_base=None, runtime_base=None):
        captured.append((spool_base, runtime_base))
        raise StopAfterRuntimeSelection

    monkeypatch.setattr(controller_module.RuntimePaths, "create", capture_create)
    monkeypatch.delenv("CAD_TEMP_DIR", raising=False)
    monkeypatch.delenv("CAD_HOME", raising=False)
    monkeypatch.chdir(launch)

    with pytest.raises(StopAfterRuntimeSelection):
        controller_module.run_session(
            workspace,
            "/bin/true",
            str(codex_stub),
            300,
            shared_spool=True,
        )

    assert captured == [(launch / ".sico" / "ai", launch / ".sico" / "ai")]
    assert not (workspace / ".cad").exists()
