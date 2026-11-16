from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

AI_ROOT = Path(__file__).resolve().parents[2]


def _skill_string(value: str | Path) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def test_active_session_is_reused_and_stopped_by_closing_stdin(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    capture = tmp_path / "controller-input.jsonl"
    shell = f"cat > {shlex.quote(str(capture))}"
    command = f"exec /bin/sh -c {shlex.quote(shell)}"
    lines = [
        f"load({_skill_string(AI_ROOT / 'skill/AI_protocol.il')})",
        f"load({_skill_string(AI_ROOT / 'skill/AI_ipc.il')})",
        f"first=aiIpcStart({_skill_string(command)} {_skill_string(tmp_path)})",
        'aiIpcHandleLine(first "{\\"event\\":\\"session.ready\\",'
        '\\"control_protocol\\":\\"control.v2\\"}")',
        f"second=aiIpcStart(\"exec sleep 30\" {_skill_string(tmp_path / 'other')})",
        "third=aiIpcActivate()",
        "if(and(first second==first third==first aiIpcSessionAliveP() "
        "aiIpcState['workspace]=="
        f"{_skill_string(tmp_path)}) then printf(\"AI_SESSION_REUSE_OK\\n\"))",
        "aiStop()",
        "ipcWait(first)",
        "if(and(!aiIpcSessionAliveP() !aiIpcState['cid] "
        "aiIpcState['exitCleanupRegistered] aiExitCleanup()) "
        "then printf(\"AI_SESSION_CLEANUP_OK\\n\"))",
        "exit()",
    ]
    completed = subprocess.run(
        [dbaccess],
        input="\n".join(lines),
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=30,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "AI_SESSION_REUSE_OK" in output
    assert "AI_SESSION_CLEANUP_OK" in output
    assert "*Error*" not in output
    assert capture.read_text(encoding="utf-8").splitlines() == [
        '{"event":"terminal.show"}',
        '{"event":"terminal.show"}',
    ]


def test_ready_without_control_protocol_is_rejected(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    lines = [
        f"load({_skill_string(AI_ROOT / 'skill/AI_protocol.il')})",
        f"load({_skill_string(AI_ROOT / 'skill/AI_ipc.il')})",
        f'cid=aiIpcStart("exec sleep 30" {_skill_string(tmp_path)})',
        "ipcSleepMilli(100)",
        'aiIpcHandleLine(cid "{\\"event\\":\\"session.ready\\"}")',
        "ipcWait(cid)",
        "if(and(!ipcIsAliveProcess(cid) !aiIpcState['cid]) "
        "then printf(\"AI_INCOMPATIBLE_SESSION_STOP_OK\\n\"))",
        "exit()",
    ]
    completed = subprocess.run(
        [dbaccess],
        input="\n".join(lines),
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=30,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "AI_INCOMPATIBLE_SESSION_STOP_OK" in output
    assert "does not support persistent session control" in output
    assert "*Error*" not in output


def test_late_ready_does_not_revive_a_retiring_session(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    lines = [
        f"load({_skill_string(AI_ROOT / 'skill/AI_protocol.il')})",
        f"load({_skill_string(AI_ROOT / 'skill/AI_ipc.il')})",
        'aiIpcState[\'phase]="retiring"',
        'aiIpcState[\'controllerProtocol]="control.retiring"',
        'aiIpcHandleLine(nil "{\\"event\\":\\"session.ready\\",'
        '\\"control_protocol\\":\\"control.v2\\"}")',
        "if(and(aiIpcState['phase]==\"retiring\" "
        'aiIpcState[\'controllerProtocol]=="control.retiring") '
        'then printf("AI_LATE_READY_IGNORED_OK\\n"))',
        "exit()",
    ]
    completed = subprocess.run(
        [dbaccess],
        input="\n".join(lines),
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=30,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "AI_LATE_READY_IGNORED_OK" in output
    assert "*Error*" not in output


def test_failed_show_retires_the_unwritable_session(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    lines = [
        f"load({_skill_string(AI_ROOT / 'skill/AI_protocol.il')})",
        f"load({_skill_string(AI_ROOT / 'skill/AI_ipc.il')})",
        f'cid=aiIpcStart("exec sleep 30" {_skill_string(tmp_path)})',
        'aiIpcState[\'controllerProtocol]="control.v2"',
        "ipcSleepMilli(100)",
        "procedure(aiIpcWrite(mockCid payload) nil)",
        "activated=aiIpcActivate()",
        "ipcWait(cid)",
        "if(and(!activated !ipcIsAliveProcess(cid) !aiIpcState['cid]) "
        "then printf(\"AI_UNWRITABLE_SESSION_STOP_OK\\n\"))",
        "exit()",
    ]
    completed = subprocess.run(
        [dbaccess],
        input="\n".join(lines),
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=30,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "AI_UNWRITABLE_SESSION_STOP_OK" in output
    assert "*Error*" not in output


def test_pending_control_handshake_times_out(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    lines = [
        f"load({_skill_string(AI_ROOT / 'skill/AI_protocol.il')})",
        f"load({_skill_string(AI_ROOT / 'skill/AI_ipc.il')})",
        f'cid=aiIpcStart("exec sleep 30" {_skill_string(tmp_path)})',
        "generation=aiIpcState['generation]",
        "ipcSleepMilli(100)",
        "aiIpcReadyTimeout(generation)",
        "ipcWait(cid)",
        "if(and(!ipcIsAliveProcess(cid) !aiIpcState['cid]) "
        "then printf(\"AI_CONTROL_HANDSHAKE_TIMEOUT_OK\\n\"))",
        "exit()",
    ]
    completed = subprocess.run(
        [dbaccess],
        input="\n".join(lines),
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=30,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "AI_CONTROL_HANDSHAKE_TIMEOUT_OK" in output
    assert "did not confirm persistent session control" in output
    assert "*Error*" not in output


def test_loading_control_protocol_stops_a_legacy_controller(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    legacy_ready = tmp_path / "legacy-controller.ready"
    legacy_command = (
        f"/usr/bin/touch {shlex.quote(str(legacy_ready))}; "
        "exec /bin/sleep 30"
    )
    lines = [
        f"load({_skill_string(AI_ROOT / 'skill/AI_protocol.il')})",
        'aiIpcVersion="20260723.ai.ipc.v3"',
        'aiIpcState=makeTable("aiLegacyIpcState" nil)',
        "aiIpcState['exitCleanupRegistered]=t",
        'aiIpcState[\'controllerProtocol]="control.v1"',
        f"aiIpcState['cid]=ipcBeginProcess({_skill_string(legacy_command)} \"\")",
        "legacyCid=aiIpcState['cid]",
        "for(readyAttempt 1 50 "
        f"unless(isFile({_skill_string(legacy_ready)}) ipcSleepMilli(100)))",
        f"load({_skill_string(AI_ROOT / 'skill/AI_ipc.il')})",
        "ipcWait(legacyCid)",
        "if(and(!ipcIsAliveProcess(legacyCid) !aiIpcState['cid] "
        "aiIpcRevision()==\"20260911.ai.ipc.v13\") "
        "then printf(\"AI_LEGACY_SESSION_STOP_OK\\n\"))",
        "exit()",
    ]
    completed = subprocess.run(
        [dbaccess],
        input="\n".join(lines),
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=30,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert legacy_ready.is_file(), output
    assert "AI_LEGACY_SESSION_STOP_OK" in output
    assert "*Error*" not in output


def test_loading_current_ipc_replaces_a_partially_deployed_v3(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    lines = [
        f"load({_skill_string(AI_ROOT / 'skill/AI_protocol.il')})",
        'aiIpcVersion="20260723.ai.ipc.v3"',
        'aiIpcState=makeTable("aiPartialV3IpcState" nil)',
        "aiIpcState['exitCleanupRegistered]=t",
        'aiIpcState[\'controllerProtocol]="control.v1"',
        f"load({_skill_string(AI_ROOT / 'skill/AI_ipc.il')})",
        "if(and(isCallable('aiIpcRevision) "
        'aiIpcRevision()=="20260911.ai.ipc.v13") '
        'then printf("AI_PARTIAL_V3_UPGRADE_OK\\n"))',
        "exit()",
    ]
    completed = subprocess.run(
        [dbaccess],
        input="\n".join(lines),
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=30,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "AI_PARTIAL_V3_UPGRADE_OK" in output
    assert "*Error*" not in output
