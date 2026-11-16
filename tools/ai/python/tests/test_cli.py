from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cadai.cli import main, parser


@pytest.mark.parametrize("suffix,command", [("REQUEST_TIMEOUT", "session"), ("MCP_TIMEOUT", "mcp")])
def test_new_timeout_setting_wins_and_empty_is_not_legacy(monkeypatch, suffix, command):
    monkeypatch.setenv("CAD_AI_" + suffix, "19")
    monkeypatch.setenv("SICO_AI_" + suffix, "23")
    arguments = [command] + (["--workspace", "/tmp"] if command == "session" else [])
    with pytest.warns(UserWarning):
        assert parser().parse_args(arguments).request_timeout == 23
    monkeypatch.setenv("SICO_AI_" + suffix, "")
    with pytest.warns(UserWarning), pytest.raises(ValueError):
        parser()


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1"])
def test_cli_rejects_non_finite_or_non_positive_timeout(value: str, capsys) -> None:
    assert main(["mcp", f"--request-timeout={value}"]) == 2
    assert "positive finite" in capsys.readouterr().err


def test_cli_does_not_lock_python_to_3_9(monkeypatch) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 12, 0))
    monkeypatch.setattr("cadai.cli.run_mcp_from_environment", lambda _timeout: 0)
    assert main(["mcp"]) == 0


def test_cli_help_describes_lsf_handoff() -> None:
    help_text = parser().format_help()
    assert "handoff one managed AI Assistant session" in help_text
    assert "interactive LSF" in help_text
    assert "==SUPPRESS==" not in help_text


def test_shared_spool_session_marker_is_hidden(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        parser().parse_args(["session", "--help"])
    assert caught.value.code == 0
    assert "--shared-spool" not in capsys.readouterr().out


def test_cli_routes_claude_session_selection(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_run_session(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return 0

    monkeypatch.setattr("cadai.cli.run_session", fake_run_session)
    assert (
        main(
            [
                "session",
                "--workspace",
                str(tmp_path),
                "--agent",
                "claude",
                "--terminal",
                "/bin/terminal",
                "--claude",
                "/bin/claude",
            ]
        )
        == 0
    )
    assert captured["args"][:3] == (tmp_path, "/bin/terminal", None)
    assert captured["kwargs"] == {
        "agent": "claude",
        "claude": "/bin/claude",
        "shared_spool": False,
    }


def test_cli_hands_complete_session_command_to_lsf(monkeypatch, tmp_path) -> None:
    captured = []
    custom_entry = tmp_path / "custom sico-ai entry"
    custom_entry.write_text("from cadai.cli import main\n", encoding="utf-8")
    monkeypatch.setattr("cadai.cli.exec_lsf_session", captured.append)
    monkeypatch.setattr(sys, "argv", [str(custom_entry)])

    assert (
        main(
            [
                "lsf-session",
                "--workspace",
                str(tmp_path),
                "--agent",
                "claude",
                "--terminal",
                "/remote/terminal",
                "--claude",
                "/remote/claude",
                "--request-timeout",
                "45",
            ]
        )
        == 0
    )
    command = captured[0]
    assert command[:3] == [sys.executable, command[1], "session"]
    assert command[1] == str(custom_entry)
    assert command[3:] == [
        "--shared-spool",
        "--workspace",
        str(tmp_path.resolve()),
        "--agent",
        "claude",
        "--request-timeout",
        "45",
        "--terminal",
        "/remote/terminal",
        "--claude",
        "/remote/claude",
    ]


def test_cli_module_entry_reenters_remote_session_via_script(monkeypatch, tmp_path) -> None:
    captured = []
    python_root = Path(__file__).resolve().parents[1]
    module_entry = python_root / "cadai" / "__main__.py"
    script_entry = python_root / "sico-ai"
    monkeypatch.setattr("cadai.cli.exec_lsf_session", captured.append)
    monkeypatch.setattr(sys, "argv", [str(module_entry)])

    assert main(["lsf-session", "--workspace", str(tmp_path)]) == 0
    assert captured[0][:3] == [sys.executable, str(script_entry.resolve()), "session"]


def test_cli_module_entry_falls_back_to_remote_module_invocation(
    monkeypatch, tmp_path
) -> None:
    captured = []
    module_entry = tmp_path / "installed" / "cadai" / "__main__.py"
    module_entry.parent.mkdir(parents=True)
    module_entry.write_text("from .cli import main\n", encoding="ascii")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr("cadai.cli.exec_lsf_session", captured.append)
    monkeypatch.setattr(sys, "argv", [str(module_entry)])

    assert main(["lsf-session", "--workspace", str(workspace)]) == 0
    assert captured[0][:4] == [sys.executable, "-m", "cadai", "session"]


def test_cli_native_entry_reenters_lsf_without_python_script_dispatch(monkeypatch, tmp_path):
    captured = []
    native = tmp_path / "sico-ai"
    native.write_bytes(b"\x7fELF" + bytes(64))
    alias = tmp_path / "shared-cad-ai"
    alias.symlink_to(native)
    monkeypatch.setattr("cadai.cli.exec_lsf_session", captured.append)
    monkeypatch.setattr(sys, "argv", [str(alias)])
    monkeypatch.setattr(
        "cadai.cli.production_python",
        lambda **kwargs: pytest.fail("native entries must not be passed to Python"),
    )
    assert main(["lsf-session", "--workspace", str(tmp_path)]) == 0
    assert captured[0][:3] == [str(alias), "session", "--shared-spool"]
