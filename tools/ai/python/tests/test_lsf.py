from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

import cadai.lsf as lsf_module
from cadai.lsf import (
    LSF_HANDOFF_ENV,
    LSF_SUBMIT_DISPLAY_ENV,
    LSF_XFORWARD_ENV,
    build_lsf_submission_command,
    exec_lsf_session,
)


def _write_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    path.chmod(0o755)
    return path


def test_lsf_arguments_are_split_without_shell_evaluation(tmp_path: Path) -> None:
    launcher = _write_executable(tmp_path / "site bsub")
    marker = tmp_path / "must-not-exist"
    environment = {
        "PROJ_AI_LSF_EXE": str(launcher),
        "PROJ_AI_LSF_ARGS": f"-I -q 'interactive queue' '$(touch {marker})'",
        "PATH": "/usr/bin:/bin",
    }

    remote_command = [
        "/shared/python",
        "/shared/sico-ai",
        "session",
        "--workspace",
        f"/project/$(touch {marker})",
    ]
    command = build_lsf_submission_command(remote_command, environment)

    assert command[:-1] == [
        str(launcher.resolve()),
        "-I",
        "-q",
        "interactive queue",
        f"$(touch {marker})",
    ]
    assert shlex.split(command[-1]) == remote_command
    assert not marker.exists()


def test_lsf_executable_can_be_resolved_from_configured_path(tmp_path: Path) -> None:
    launcher = _write_executable(tmp_path / "bsub")
    command = build_lsf_submission_command(
        ["/bin/true"],
        {
            "PROJ_AI_LSF_EXE": "bsub",
            "PROJ_AI_LSF_ARGS": "-I",
            "PATH": str(tmp_path),
        },
    )
    assert command == [str(launcher.resolve()), "-I", "/bin/true"]


def test_standard_bsub_accepts_interactive_x11_forwarding(tmp_path: Path) -> None:
    launcher = _write_executable(tmp_path / "bsub")
    command = build_lsf_submission_command(
        ["/bin/true"],
        {
            "PROJ_AI_LSF_EXE": str(launcher),
            "PROJ_AI_LSF_ARGS": "-I -XF -q ai",
        },
    )
    assert command == [str(launcher.resolve()), "-I", "-XF", "-q", "ai", "/bin/true"]


def test_resolved_bsub_symlink_still_requires_interactive_mode(tmp_path: Path) -> None:
    launcher = _write_executable(tmp_path / "bsub")
    alias = tmp_path / "site-launcher"
    alias.symlink_to(launcher)
    with pytest.raises(ValueError, match="plain bsub -I mode"):
        build_lsf_submission_command(
            ["/bin/true"],
            {"PROJ_AI_LSF_EXE": str(alias), "PROJ_AI_LSF_ARGS": "-q ai"},
        )


def test_site_wrapper_symlink_is_executed_through_configured_name(tmp_path: Path) -> None:
    wrapper = _write_executable(tmp_path / "site-wrapper")
    alias = tmp_path / "queue-a"
    alias.symlink_to(wrapper)

    command = build_lsf_submission_command(
        ["/bin/true"],
        {"PROJ_AI_LSF_EXE": str(alias), "PROJ_AI_LSF_ARGS": "-q ai"},
    )

    assert command == [str(alias.absolute()), "-q", "ai", "/bin/true"]


@pytest.mark.parametrize(
    "arguments",
    (
        "-q ai",
        "-q -I",
        "-- -I",
        "true -I",
        "-Is -q ai",
        "-Ip -q ai",
        "-I -Is -q ai",
        "-I -Ip -q ai",
    ),
)
def test_standard_bsub_requires_plain_interactive_mode(
    tmp_path: Path, arguments: str
) -> None:
    launcher = _write_executable(tmp_path / "bsub")
    with pytest.raises(ValueError, match="plain bsub -I mode"):
        build_lsf_submission_command(
            ["/bin/true"],
            {"PROJ_AI_LSF_EXE": str(launcher), "PROJ_AI_LSF_ARGS": arguments},
        )


@pytest.mark.parametrize(
    "arguments",
    (
        "-I -i input.txt",
        "-I -is input.txt",
        "-I -o output.txt",
        "-I -oo output.txt",
        "-I -o=output.txt",
    ),
)
def test_standard_bsub_rejects_control_channel_redirection(
    tmp_path: Path, arguments: str
) -> None:
    launcher = _write_executable(tmp_path / "bsub")
    with pytest.raises(ValueError, match="must not redirect bsub stdin or stdout"):
        build_lsf_submission_command(
            ["/bin/true"],
            {"PROJ_AI_LSF_EXE": str(launcher), "PROJ_AI_LSF_ARGS": arguments},
        )


@pytest.mark.parametrize(
    ("environment", "message"),
    (
        ({}, "PROJ_AI_LSF_EXE is not configured"),
        ({"PROJ_AI_LSF_EXE": "missing", "PATH": ""}, "cannot execute"),
        (
            {"PROJ_AI_LSF_EXE": "/bin/true", "PROJ_AI_LSF_ARGS": "'unterminated"},
            "invalid PROJ_AI_LSF_ARGS",
        ),
        (
            {"PROJ_AI_LSF_EXE": "/bin/true", "PROJ_AI_LSF_ARGS": "'bad\0value'"},
            "contains a NUL byte",
        ),
    ),
)
def test_invalid_lsf_configuration_is_rejected(
    environment: dict[str, str], message: str
) -> None:
    with pytest.raises((FileNotFoundError, ValueError), match=message):
        build_lsf_submission_command(["/bin/true"], environment)


def test_exec_replaces_process_and_removes_consumed_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    launcher = _write_executable(tmp_path / "bsub")
    captured = {}

    class ExecCalled(Exception):
        pass

    def fake_execvpe(executable, command, environment):
        captured["executable"] = executable
        captured["command"] = command
        captured["environment"] = environment
        raise ExecCalled

    monkeypatch.setattr(lsf_module.os, "execvpe", fake_execvpe)
    environment = {
        "PROJ_AI_LSF_EXE": str(launcher),
        "PROJ_AI_LSF_ARGS": "-I -XF -q ai",
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/cad-user",
        "DISPLAY": ":0",
        "LSB_SSH_XFORWARD_CMD": "/site/bin/managed-lsf-x11-ssh",
        "BSUB_QUIET": "yes",
        "LSB_EXPORT_ENV": "site-policy",
        "KEEP_ME": "yes",
    }

    with pytest.raises(ExecCalled):
        exec_lsf_session(["/shared/python", "/shared/sico-ai", "session"], environment)

    assert captured["executable"] == str(launcher.resolve())
    assert captured["command"] == [
        str(launcher.resolve()),
        "-I",
        "-XF",
        "-q",
        "ai",
        "/shared/python /shared/sico-ai session",
    ]
    assert captured["environment"]["KEEP_ME"] == "yes"
    assert captured["environment"]["HOME"] == "/home/cad-user"
    assert (
        captured["environment"]["LSB_SSH_XFORWARD_CMD"]
        == "/site/bin/managed-lsf-x11-ssh"
    )
    assert captured["environment"][LSF_HANDOFF_ENV] == "1"
    assert captured["environment"][LSF_SUBMIT_DISPLAY_ENV] == ":0"
    assert captured["environment"][LSF_XFORWARD_ENV] == "1"
    assert captured["environment"]["BSUB_QUIET"] == "yes"
    assert captured["environment"]["LSB_EXPORT_ENV"] == "site-policy"
    assert "PROJ_AI_LSF_EXE" not in captured["environment"]
    assert "PROJ_AI_LSF_ARGS" not in captured["environment"]


def test_standard_bsub_without_xf_is_quiet_and_emits_no_warning(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    launcher = _write_executable(tmp_path / "bsub")

    class ExecCalled(Exception):
        pass

    environment_seen = {}

    def fake_execvpe(_executable, _command, environment):
        environment_seen.update(environment)
        raise ExecCalled

    monkeypatch.setattr(lsf_module.os, "execvpe", fake_execvpe)
    with pytest.raises(ExecCalled):
        exec_lsf_session(
            ["/shared/python", "/shared/sico-ai", "session"],
            {
                "PROJ_AI_LSF_EXE": str(launcher),
                "PROJ_AI_LSF_ARGS": "-I -q ai",
                "DISPLAY": ":0",
                "BSUB_QUIET": "   ",
            },
        )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert environment_seen["BSUB_QUIET"] == "1"
    assert "LSB_EXPORT_ENV" not in environment_seen


def test_standard_bsub_xf_gets_noninteractive_ssh_and_x11_environment(
    tmp_path: Path, monkeypatch
) -> None:
    launcher = _write_executable(tmp_path / "bsub")

    class ExecCalled(Exception):
        pass

    environment_seen = {}

    def fake_execvpe(_executable, _command, environment):
        environment_seen.update(environment)
        raise ExecCalled

    monkeypatch.setattr(lsf_module.os, "execvpe", fake_execvpe)
    with pytest.raises(ExecCalled):
        exec_lsf_session(
            ["/shared/python", "/shared/sico-ai", "session"],
            {
                "PROJ_AI_LSF_EXE": str(launcher),
                "PROJ_AI_LSF_ARGS": "-I -XF -q ai",
                "DISPLAY": ":0",
                "XAUTHORITY": "/tmp/Xauthority",
            },
        )

    assert environment_seen["BSUB_QUIET"] == "1"
    assert "LSB_EXPORT_ENV" not in environment_seen
    assert environment_seen["LSB_SSH_XFORWARD_CMD"] == (
        "ssh -X -n -o BatchMode=yes -o StrictHostKeyChecking=yes"
    )
    assert environment_seen["DISPLAY"] == ":0"
    assert environment_seen["XAUTHORITY"] == "/tmp/Xauthority"


def test_real_exec_handoff_preserves_remote_arguments_and_cleans_environment() -> None:
    remote_program = (
        "import json, os, sys; "
        "print(json.dumps({'arguments': sys.argv[1:], "
        "'lsf_exe': os.environ.get('PROJ_AI_LSF_EXE'), "
        "'lsf_args': os.environ.get('PROJ_AI_LSF_ARGS')}))"
    )
    remote_command = [sys.executable, "-c", remote_program, "argument with spaces", "$(literal)"]
    driver = (
        "from cadai.lsf import exec_lsf_session; "
        f"exec_lsf_session({remote_command!r})"
    )
    environment = os.environ.copy()
    environment.update(
        {
            "PROJ_AI_LSF_EXE": "/bin/sh",
            "PROJ_AI_LSF_ARGS": "-c",
        }
    )

    completed = subprocess.run(
        [sys.executable, "-c", driver],
        check=True,
        text=True,
        capture_output=True,
        env=environment,
        cwd=Path(__file__).resolve().parents[1],
        timeout=5,
    )

    payload = json.loads(completed.stdout)
    assert payload == {
        "arguments": ["argument with spaces", "$(literal)"],
        "lsf_exe": None,
        "lsf_args": None,
    }


def test_skill_ready_timer_uses_remote_aware_delay() -> None:
    skill_root = Path(__file__).resolve().parents[2] / "skill"
    ipc_source = (skill_root / "AI_ipc.il").read_text(encoding="utf-8")
    launcher_source = (skill_root / "AI_launcher.il").read_text(encoding="utf-8")
    assert 'aiEnvValue("PROJ_AI_LSF_EXE")' in launcher_source
    assert 'hiRegTimer(sprintf(nil "aiIpcReadyTimeout(%d)" generation)' in ipc_source
    assert "aiControllerReadyDelay()" in ipc_source
    assert "'aiIpcPostFunc \"\"" in ipc_source
    assert '?choices list("Codex" "Claude")' in launcher_source
    assert '?value "Claude"' in launcher_source
    assert '?defValue "Claude"' in launcher_source
