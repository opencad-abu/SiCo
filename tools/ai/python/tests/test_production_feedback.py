from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import cadai.controller as controller_module
import cadai.runtime as runtime_module
from cadai.controller import RequestDispatcher
from cadai.launch import build_claude_command, build_codex_command, prepare_terminal_environment
from cadai.lsf import capture_virtuoso_environment
from cadai.runtime import RuntimePaths, ai_temp_root, read_spool
from cadai.socket_server import RequestFailure


@pytest.mark.parametrize("transient_errno", [errno.ENOENT, errno.ESTALE])
def test_acknowledged_result_retries_nas_read_without_reexecuting(
    tmp_path: Path, monkeypatch, transient_errno: int
) -> None:
    runtime = RuntimePaths.create(runtime_base=tmp_path / ".sico" / "ai")
    requests = []

    class Transport:
        def request(self, payload, timeout):
            requests.append(payload)
            Path(payload["output_path"]).write_text('{"ok":true,"cwd":"/shared"}')
            return True, {"result_path": payload["output_path"]}

    real_open = runtime_module.os.open
    attempts = []

    def delayed_open(path, flags, *args, **kwargs):
        if Path(path).name.startswith("result-"):
            attempts.append(path)
            if len(attempts) <= 2:
                raise OSError(transient_errno, os.strerror(transient_errno))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(runtime_module.os, "open", delayed_open)
    try:
        result = RequestDispatcher(Transport(), runtime, tmp_path)("a" * 32, "get_context", {})
        assert result == {"ok": True, "cwd": "/shared"}
        assert len(requests) == 1
        assert len(attempts) == 3
        assert list(runtime.spool.iterdir()) == []
    finally:
        runtime.cleanup()


def test_missing_result_is_bounded_and_reports_real_errno(tmp_path: Path, monkeypatch) -> None:
    runtime = RuntimePaths.create(runtime_base=tmp_path / ".sico" / "ai")
    monkeypatch.setattr(controller_module, "RESULT_VISIBILITY_TIMEOUT", 0.01)
    requests = []

    class Transport:
        def request(self, payload, timeout):
            requests.append(payload)
            return True, {"result_path": payload["output_path"]}

    try:
        with pytest.raises(RequestFailure, match="No such file") as failure:
            RequestDispatcher(Transport(), runtime, tmp_path)("a" * 32, "get_context", {})
        assert failure.value.code == "invalid_result"
        assert len(requests) == 1
    finally:
        runtime.cleanup()


def test_skill_failure_without_result_preserves_bridge_diagnostic(tmp_path: Path) -> None:
    runtime = RuntimePaths.create(runtime_base=tmp_path / ".sico" / "ai")

    class Transport:
        def request(self, payload, timeout):
            return False, {"message": "Virtuoso context collection failed: shared path unavailable"}

    try:
        with pytest.raises(RequestFailure, match="shared path unavailable") as failure:
            RequestDispatcher(Transport(), runtime, tmp_path)("a" * 32, "get_context", {})
        assert failure.value.code == "skill_error"
    finally:
        runtime.cleanup()


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory"])
def test_spool_rejects_nonregular_files_without_blocking(tmp_path: Path, kind: str) -> None:
    tmp_path.chmod(0o700)
    path = tmp_path / "payload"
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_text("secret")
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.mkdir()
    with pytest.raises(ValueError, match="regular"):
        read_spool(tmp_path, path.name)


def test_shared_temp_preserves_mount_alias_and_normalizes_legacy_root(tmp_path: Path) -> None:
    physical = tmp_path / "host-only-volume"
    physical.mkdir()
    (physical / ".cad").mkdir(mode=0o700)
    shared = tmp_path / "shared-nas"
    shared.symlink_to(physical, target_is_directory=True)
    temporary = ai_temp_root(shared / ".cad")
    assert temporary == shared / ".cad" / "ai"
    assert ai_temp_root(temporary) == temporary
    runtime = RuntimePaths.create(runtime_base=temporary, spool_base=temporary)
    try:
        assert runtime.root.is_relative_to(temporary)
        assert runtime.virtuoso_spool.is_relative_to(temporary)
        assert str(physical) not in str(runtime.virtuoso_spool)
        assert RuntimePaths.from_root(runtime.root).root == runtime.root
    finally:
        runtime.cleanup()


def test_snapshot_artifact_accepts_preserved_shared_mount_path(tmp_path: Path) -> None:
    physical = tmp_path / "host-only-volume"
    physical.mkdir()
    shared = tmp_path / "shared-nas"
    shared.symlink_to(physical, target_is_directory=True)
    temporary = ai_temp_root(shared / ".sico")
    runtime = RuntimePaths.create(runtime_base=temporary, spool_base=temporary)
    source = runtime.virtuoso_spool / "snapshot-data-probe.jsonl"
    source.write_text('{"kind":"probe"}\n')
    try:
        result = RequestDispatcher(None, runtime, shared)._materialize_snapshot_artifact(
            {"artifact_path": str(source)}, source
        )
        artifact = Path(result["artifact"]["path"])
        assert artifact.read_text() == '{"kind":"probe"}\n'
        assert artifact.is_relative_to(temporary)
        assert not source.exists()
    finally:
        runtime.cleanup()


@pytest.mark.parametrize("agent", ["codex", "claude"])
def test_submit_host_modules_survive_agent_environment_sanitizing(
    tmp_path: Path, monkeypatch, agent: str
) -> None:
    submit = {
        "PATH": "/site/IC618/bin:/site/lsf/bin:/usr/bin",
        "LD_LIBRARY_PATH": "/site/IC618/lib",
        "PYTHONPATH": "/site/cadence/python",
        "MODULEPATH": "/site/modulefiles",
        "LOADEDMODULES": "IC06.18.350:LSF",
        "_LMFILES_": "/site/modulefiles/IC06.18.350:/site/modulefiles/LSF",
        "MODULESHOME": "/site/modules",
        "LMOD_CMD": "/site/lmod/libexec/lmod",
    }
    captured = dict(submit)
    capture_virtuoso_environment(captured)
    for name, value in captured.items():
        monkeypatch.setenv(name, value)
    # Simulate a site prolog selecting a different environment on the agent.
    monkeypatch.setenv("LOADEDMODULES", "agent-python")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("CAD_AI_PYTHON_LIBRARY_PATH", "/production/python/lib")
    monkeypatch.delenv("CAD_TEMP_DIR", raising=False)
    monkeypatch.setenv("SICO_TEMP_DIR", str(tmp_path / ".sico"))
    runtime = RuntimePaths.create()
    try:
        env, _warnings = prepare_terminal_environment(runtime, "secret", tmp_path, tmp_path, agent)
        for name, value in submit.items():
            assert env[f"SICO_VIRTUOSO_{name}"] == value
        assert env["LD_LIBRARY_PATH"] == "/production/python/lib"
        assert "PYTHONPATH" not in env
        assert env["SICO_TEMP_DIR"] == str(tmp_path / ".sico")
        assert "CAD_TEMP_DIR" not in env
    finally:
        runtime.cleanup()


def test_startup_context_is_present_in_both_clients_and_profile_aware(tmp_path: Path) -> None:
    for profile in ("interactive", "verification"):
        codex = build_codex_command(
            "/bin/codex",
            '/shared/Python "3"/bin/python3',
            tmp_path,
            tmp_path,
            10,
            environment={},
            profile=profile,
        )
        text = json.loads(
            next(x.split("=", 1)[1] for x in codex if x.startswith("developer_instructions="))
        )
        claude = build_claude_command(
            "/bin/claude", tmp_path / "mcp", environment={}, profile=profile
        )
        assert claude[claude.index("--append-system-prompt") + 1] == text
        for expected in (
            "Virtuoso",
            "get_context",
            '"$CAD_PYTHON" -s',
            ".cad/ai",
            "SICO_VIRTUOSO_MODULEPATH",
        ):
            assert expected in text
        if profile == "interactive":
            assert "eval_skill" in text
        else:
            assert "eval_skill" not in text
            assert "这是校验配置" in text
        assert any(x.startswith("shell_environment_policy.set={CAD_PYTHON=") for x in codex)


def test_ipc_context_error_does_not_wedge_following_request(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    skill = Path(__file__).resolve().parents[2] / "skill"
    output = tmp_path / "context.json"
    responses = tmp_path / "responses.jsonl"
    lines = [
        f"load({json.dumps(str(skill / name))})"
        for name in ("AI_protocol.il", "AI_eval.il", "AI_ipc.il")
    ]
    lines += [
        f"probeResponsePort=outfile({json.dumps(str(responses))})",
        'procedure(aiIpcWrite(cid payload) fprintf(probeResponsePort "%s\\n" payload) t)',
        "probeFailContext=t",
        'aiIpcState[\'responded]=makeTable("probeResponded" nil)',
        'procedure(aiWorkingDirectory() '
        'if(probeFailContext then error("probe context failed") else "/shared"))',
        'probeRequest=makeTable("request" nil)',
        'probeRequest[\'id]="first" probeRequest[\'method]="get_context"',
        f"probeRequest['outputPath]={json.dumps(str(output))}",
        "aiIpcState['queue]=list(probeRequest) aiIpcDrainQueue(nil)",
        'probeFailContext=nil probeRequest[\'id]="second"',
        "aiIpcState['queue]=list(probeRequest) aiIpcDrainQueue(nil)",
        "close(probeResponsePort)",
        'when(!aiIpcState[\'busy] printf("AI_IPC_RECOVERY_OK\\n"))',
        "exit()",
    ]
    completed = subprocess.run(
        [dbaccess], input="\n".join(lines), text=True, capture_output=True, cwd=tmp_path, timeout=30
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "AI_IPC_RECOVERY_OK" in completed.stdout, completed.stdout + completed.stderr
    first, second = [json.loads(line) for line in responses.read_text().splitlines()]
    assert first["ok"] is False
    assert "probe context failed" in first["error"]["message"]
    assert second["ok"] is True
    assert json.loads(output.read_text())["cwd"] == "/shared"
