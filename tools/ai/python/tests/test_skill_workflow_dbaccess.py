from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

AI_ROOT = Path(__file__).resolve().parents[2]


def _skill_string(value: str | Path) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def test_workflow_lifecycle_reload_and_job_limit_with_dbaccess(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    flow_file = AI_ROOT / "skill/AI_flows.il"
    config = tmp_path / "flow'$(touch AI_WORKFLOW_INJECTED).toml"
    config.write_text("[probe]\nvalue = 1\n", encoding="utf-8")
    alias = tmp_path / "flow-alias.toml"
    alias.symlink_to(config)
    injected = tmp_path / "AI_WORKFLOW_INJECTED"
    large_output = tmp_path / "large_output.py"
    large_output.write_text(
        "import sys\nsys.stdout.buffer.write(b'\\xe4\\xb8\\xad' * 700000)\n"
        "sys.stdout.buffer.flush()\n",
        encoding="utf-8",
    )
    tree_probe = tmp_path / "tree_probe.py"
    tree_pids = tmp_path / "tree.pids"
    tree_ready = tmp_path / "tree.ready"
    production_python_root = tmp_path / "production-python"
    production_python = production_python_root / "bin" / "python3"
    production_python.parent.mkdir(parents=True)
    production_python.symlink_to(Path(sys.executable).resolve())
    tree_probe.write_text(
        """import os
import signal
import subprocess
import sys
import time

role, pid_file, ready_file = sys.argv[1:]
for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
    signal.signal(signum, signal.SIG_IGN)
fd = os.open(pid_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
os.write(fd, f"{role} {os.getpid()} {os.getppid()} {os.getsid(0)}\\n".encode())
os.close(fd)
if role == "root":
    subprocess.Popen([sys.executable, __file__, "middle", pid_file, ready_file])
elif role == "middle":
    subprocess.Popen(
        [sys.executable, __file__, "leaf", pid_file, ready_file],
        start_new_session=True,
    )
else:
    with open(ready_file, "w", encoding="utf-8") as stream:
        stream.write("ready\\n")
while True:
    time.sleep(1)
""",
        encoding="utf-8",
    )
    clean_python = (
        "env",
        "-u",
        "PYTHONHOME",
        "-u",
        "PYTHONPATH",
        "-u",
        "LD_PRELOAD",
        "-u",
        "LD_AUDIT",
        "LD_LIBRARY_PATH=",
        sys.executable,
    )
    large_command = "exec " + " ".join(
        map(shlex.quote, (*clean_python, str(large_output)))
    )
    tree_command = "exec " + " ".join(
        map(
            shlex.quote,
            (*clean_python, str(tree_probe), "root", str(tree_pids), str(tree_ready)),
        )
    )
    lines = [
        f'load({_skill_string(AI_ROOT.parent / "common/skill/SICO_environment.il")})',
        f'load({_skill_string(AI_ROOT.parent / "common/skill/SICO_installation.il")})',
        f'load({_skill_string(AI_ROOT.parent / "common/skill/SICO_state.il")})',
        f'load({_skill_string(AI_ROOT.parent / "common/skill/SICO_commands.il")})',
        f'setShellEnvVar("SICO_PYTHON" {_skill_string(sys.executable)})',
        "aiProbeFailures=0",
        "procedure(aiProbeCheck(condition label) "
        "unless(condition aiProbeFailures=aiProbeFailures+1 "
        'printf("AI_WORKFLOW_PROBE_FAIL:%s\\n" label)) condition)',
        "procedure(aiProbeFileContains(path needle) "
        "let((port line found) port=infile(path) "
        "when(port while(and(!found gets(line port)) "
        "when(index(line needle) found=t)) close(port)) found))",
        f"flowFile={_skill_string(flow_file)}",
        f"flowCommands={_skill_string(flow_file.with_name('AI_flow_command.il'))}",
        f"flowPath={_skill_string(config)}",
        f"flowAlias={_skill_string(alias)}",
        f"largeCommand={_skill_string(large_command)}",
        f"treeCommand={_skill_string(tree_command)}",
        f"treeReady={_skill_string(tree_ready)}",
        "load(flowCommands) load(flowFile)",
        'setShellEnvVar("CAD_PYTHON" "")',
        'setShellEnvVar("DRC_PYTHON" "")',
        'setShellEnvVar("RCE_PYTHON" "")',
        f'setShellEnvVar("CAD_PYTHON_ROOT" {_skill_string(production_python_root)})',
        f'aiProbeCheck(aiWorkflowPython("drc")=={_skill_string(sys.executable)} '
        '"new_python_precedes_legacy_root")',
        'aiProbeCheck(aiWorkflowRevision()=="20261024.sico.flow.v8" "revision")',
        "runtimeProbe=aiWorkflowCreateRuntimeDir()",
        'runtimePrefix=strcat(aiWorkflowLaunchDir "/.sico/cadflow")',
        'aiProbeCheck(index(aiWorkflowTempEnvironment() "SICO_TEMP_DIR=") '
        '"temp_environment")',
        'aiProbeCheck(index(aiWorkflowTempEnvironment() "XDG_RUNTIME_DIR=") '
        '"xdg_environment")',
        'aiProbeCheck(index(aiWorkflowCommand("drc" "/tmp/drc.toml" "run" nil nil '
        '"/usr/bin/python3" "/tmp/cad-flow") "PYTHONDONTWRITEBYTECODE=1") '
        '"no_bytecode")',
        'aiProbeCheck(substring(runtimeProbe 1 strlen(runtimePrefix))==runtimePrefix '
        '"runtime_cwd")',
        'aiProbeCheck(and(isDir(strcat(aiWorkflowLaunchDir "/.sico/cache")) '
        'isDir(strcat(aiWorkflowLaunchDir "/.sico/runtime"))) "xdg_directories")',
        "deleteDir(runtimeProbe)",
        'aiProbeCheck(aiWorkflowInputPath(flowAlias)==flowPath "symlink_resolution")',
        'procedure(aiWorkflowToolsRoot() "/tmp")',
        'procedure(aiWorkflowPython(flow) "/definitely/missing/cad-flow-python")',
        'procedure(aiWorkflowEntry(flow toolsRoot) "/bin/true")',
        'missingRuntime=aiWorkflowStart("rce" flowPath "run" nil nil)',
        'aiProbeCheck(and(index(missingRuntime "\\"code\\":\\"runtime_unavailable\\"") '
        'aiWorkflowJobCount()==0) "missing_python")',
        'procedure(aiWorkflowPython(flow) "/bin/true")',
        'procedure(aiWorkflowEntry(flow toolsRoot) "/bin/true")',
        'startJson=aiWorkflowStart("rce" flowPath "run" nil nil)',
        'successId=aiWorkflowRunning[strcat("rce|" flowPath)]',
        "successJob=aiWorkflowJobs[successId]",
        "successRuntime=successJob['runtime]",
        "ipcWait(successJob['cid] 1 5)",
        "successStatus=aiWorkflowStatus(successId)",
        'aiProbeCheck(and(index(startJson "\\"ok\\":true") '
        'index(successStatus "\\"state\\":\\"completed\\"") '
        'index(successStatus "\\"exit_status\\":0") '
        'index(successStatus "\\"log_bytes\\":0") '
        'index(successStatus "\\"log_truncated\\":false") '
        "!isFile(successJob['launcher]) isFile(successJob['log])) \"success_post\")",
        'procedure(aiWorkflowPython(flow) "/bin/false")',
        'aiWorkflowStart("drc" flowPath "run" nil nil)',
        'failedId=aiWorkflowRunning[strcat("drc|" flowPath)]',
        "failedJob=aiWorkflowJobs[failedId]",
        "failedRuntime=failedJob['runtime]",
        "ipcWait(failedJob['cid] 1 5)",
        "failedStatus=aiWorkflowStatus(failedId)",
        'aiProbeCheck(and(index(failedStatus "\\"state\\":\\"failed\\"") '
        'index(failedStatus "\\"exit_status\\":1")) "failed_post")',
        'procedure(aiWorkflowPython(flow) "/bin/true")',
        "procedure(aiWorkflowCommand(flow path mode dryRun stopAfter python entry) largeCommand)",
        'aiWorkflowStart("gds" flowPath "run" nil nil)',
        'largeId=aiWorkflowRunning[strcat("gds|" flowPath)]',
        "largeJob=aiWorkflowJobs[largeId]",
        "largeRuntime=largeJob['runtime]",
        "ipcWait(largeJob['cid] 1 10)",
        "largeStatus=aiWorkflowStatus(largeId)",
        'aiProbeCheck(and(index(largeStatus "\\"exit_status\\":0") '
        'index(largeStatus "\\"log_bytes\\":1048576") '
        'index(largeStatus "\\"log_truncated\\":true") '
        "largeJob['logTruncated] largeJob['logBytes]==fileLength(largeJob['log]) "
        "fileLength(largeJob['log])==aiWorkflowLogLimit() "
        'aiProbeFileContains(largeJob[\'log] "[AI Assistant] flow log truncated")) '
        '"bounded_log")',
        "aiWorkflowCleanupJobArtifacts(largeJob)",
        'aiProbeCheck(!isDir(largeRuntime) "bounded_log_cleanup")',
        "procedure(aiWorkflowCommand(flow path mode dryRun stopAfter python entry) treeCommand)",
        'aiWorkflowStart("lvs" flowPath "run" nil nil)',
        'runningId=aiWorkflowRunning[strcat("lvs|" flowPath)]',
        "runningJob=aiWorkflowJobs[runningId]",
        "runningRuntime=runningJob['runtime]",
        "runningCid=runningJob['cid]",
        "for(probeWait 1 100 unless(isFile(treeReady) ipcSleepMilli(50)))",
        'aiProbeCheck(isFile(treeReady) "grandchild_ready")',
        'aliasDuplicate=aiWorkflowStart("lvs" flowAlias "run" nil nil)',
        'aiProbeCheck(index(aliasDuplicate "\\"code\\":\\"already_running\\"") '
        '"symlink_duplicate")',
        "savedState=aiWorkflowState",
        "savedJobs=aiWorkflowJobs",
        "load(flowCommands) load(flowFile)",
        "aiProbeCheck(and(eq(savedState aiWorkflowState) eq(savedJobs aiWorkflowJobs) "
        'aiWorkflowJobs[runningId][\'state]=="running") "same_version_reload")',
        'aiWorkflowVersion="probe.upgrade"',
        "load(flowCommands) load(flowFile)",
        "aiProbeCheck(and(eq(savedState aiWorkflowState) eq(savedJobs aiWorkflowJobs) "
        'aiWorkflowJobs[runningId][\'state]=="running") "upgrade_reload")',
        'duplicate=aiWorkflowLaunch("lvs" flowPath "run" nil nil "exec /bin/true")',
        'aiProbeCheck(index(duplicate "\\"code\\":\\"already_running\\"") "duplicate")',
        "aiWorkflowExitCleanup()",
        "cleanupStatus=aiWorkflowStatus(runningId)",
        'aiProbeCheck(and(index(cleanupStatus "\\"exit_status\\":137") '
        "!ipcIsAliveProcess(runningCid) !isDir(successRuntime) "
        '!isDir(failedRuntime) !isDir(runningRuntime)) "forced_cleanup")',
        'procedure(aiWorkflowToolsRoot() "/tmp")',
        'procedure(aiWorkflowPython(flow) "/bin/true")',
        'procedure(aiWorkflowEntry(flow toolsRoot) "/bin/true")',
        "procedure(aiWorkflowCommand(flow path mode dryRun stopAfter python entry) "
        '"exec /bin/true")',
        'aiWorkflowJobs=makeTable("aiWorkflowTerminalLimitProbe" nil)',
        'aiWorkflowByCid=makeTable("aiWorkflowTerminalLimitCidProbe" nil)',
        'aiWorkflowRunning=makeTable("aiWorkflowTerminalLimitRunningProbe" nil)',
        "aiWorkflowState['sequence]=128",
        "for(probeIndex 1 128 "
        'probeId=sprintf(nil "terminal-%03d" probeIndex) '
        'probeJob=makeTable(strcat("probe-" probeId) nil) '
        "probeJob['state]=\"completed\" probeJob['sequence]=probeIndex "
        "aiWorkflowJobs[probeId]=probeJob)",
        "evictRuntime=aiWorkflowCreateRuntimeDir()",
        'evictLog=strcat(evictRuntime "/flow.log")',
        'evictPort=outfile(evictLog "w")',
        'fprintf(evictPort "evict me\\n")',
        "close(evictPort)",
        'aiWorkflowJobs["terminal-001"][\'runtime]=evictRuntime',
        'aiWorkflowJobs["terminal-001"][\'log]=evictLog',
        'aiWorkflowStart("rce" flowPath "run" nil nil)',
        'limitId=aiWorkflowRunning[strcat("rce|" flowPath)]',
        "limitJob=aiWorkflowJobs[limitId]",
        "limitRuntime=limitJob['runtime]",
        "aiProbeCheck(and(aiWorkflowJobCount()==128 "
        '!aiWorkflowJobs["terminal-001"] aiWorkflowJobs["terminal-002"] '
        "!isFile(evictLog) !isDir(evictRuntime)) "
        '"oldest_terminal_eviction")',
        "ipcWait(limitJob['cid] 1 5)",
        "aiWorkflowCleanupJobArtifacts(limitJob)",
        'aiProbeCheck(!isDir(limitRuntime) "explicit_terminal_cleanup")',
        'aiWorkflowJobs=makeTable("aiWorkflowRunningLimitProbe" nil)',
        'aiWorkflowByCid=makeTable("aiWorkflowRunningLimitCidProbe" nil)',
        'aiWorkflowRunning=makeTable("aiWorkflowRunningLimitKeysProbe" nil)',
        "aiWorkflowState['sequence]=500",
        "for(probeIndex 1 128 "
        'probeId=sprintf(nil "running-%03d" probeIndex) '
        'probeJob=makeTable(strcat("probe-" probeId) nil) '
        "probeJob['state]=\"running\" probeJob['sequence]=probeIndex "
        "aiWorkflowJobs[probeId]=probeJob)",
        'limitJson=aiWorkflowStart("drc" flowPath "run" nil nil)',
        'aiProbeCheck(and(index(limitJson "\\"code\\":\\"job_limit\\"") '
        "aiWorkflowJobCount()==128 aiWorkflowState['sequence]==500) "
        '"all_running_limit")',
        'if(aiProbeFailures==0 then printf("AI_WORKFLOW_DBACCESS_OK\\n") '
        'else printf("AI_WORKFLOW_DBACCESS_FAILED:%d\\n" aiProbeFailures))',
        "exit()",
    ]
    records: dict[str, tuple[int, ...]] = {}
    survivors: set[int] = set()
    try:
        completed = subprocess.run(
            [dbaccess],
            input="\n".join(lines),
            text=True,
            capture_output=True,
            cwd=tmp_path,
            timeout=30,
            check=False,
        )
    finally:
        records = {
            fields[0]: tuple(map(int, fields[1:]))
            for line in (
                tree_pids.read_text(encoding="utf-8").splitlines() if tree_pids.exists() else []
            )
            if len(fields := line.split()) == 4
        }
        deadline = time.monotonic() + 3
        survivors = {values[0] for values in records.values()}
        while survivors and time.monotonic() < deadline:
            survivors = {pid for pid in survivors if Path(f"/proc/{pid}").exists()}
            if survivors:
                time.sleep(0.05)
        for pid in survivors:
            cmdline = Path(f"/proc/{pid}/cmdline")
            if cmdline.exists() and str(tree_probe).encode() in cmdline.read_bytes():
                os.kill(pid, signal.SIGKILL)
    output = completed.stdout + completed.stderr
    assert set(records) == {"root", "middle", "leaf"}, (records, output)
    assert records["middle"][1] == records["root"][0], records
    assert records["leaf"][1] == records["middle"][0], records
    assert records["leaf"][2] != records["root"][2], records
    assert not survivors, (survivors, output)
    assert completed.returncode == 0, output
    assert "AI_WORKFLOW_DBACCESS_OK" in output, output
    assert "AI_WORKFLOW_PROBE_FAIL" not in output, output
    assert "AI_WORKFLOW_DBACCESS_FAILED" not in output, output
    assert "*Error*" not in output, output
    assert "PARSER WARNING" not in output, output
    assert not injected.exists(), output
