"""Full-setup startup acceptance for the real project profiles.

Sources the project ``setup.bashrc``, starts Virtuoso in the project working
directory and drives the site entry (the real "Silicon Copilot" menu callback,
or the same two steps for a project without a ``.cdsinit``), then verifies the
routing chain and one read plus one routed request against the project copy.

Only scratch directories that still point at the old ``/workarea/xh/smic28``
project are redirected to a private directory; design libraries, PDK, cds.lib
and historical sessions are never written, and no archive maintenance runs on
the project directory.  Opt in with ``CAD_AI_RUN_ROUTER_TESTS=1`` and
``CAD_AI_ROUTER_SETUP_PROJECTS``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from sico.core.contracts import BoundContext
from sico.transport.bridge_client import BridgeClient
from sico.transport.broker import skill_call_context

CAD = Path(__file__).absolute().parents[3]
VIRTUOSO = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/virtuoso")
SCRATCH_NAMES = ("CAD_TEMP_DIR", "TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR")


def _setup_cases():
    raw = os.environ.get("CAD_AI_ROUTER_SETUP_PROJECTS", "").strip()
    if not raw:
        return []
    cases = json.loads(raw)
    assert isinstance(cases, list) and cases
    for case in cases:
        if not isinstance(case, dict) or not {
            "label", "project_dir", "setup_profile", "fingerprint", "graphical", "entry"
        } <= set(case):
            raise ValueError("Invalid CAD_AI_ROUTER_SETUP_PROJECTS entry")
    return cases


CASES = _setup_cases()
pytestmark = pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_ROUTER_TESTS") != "1" or not VIRTUOSO.is_file() or not CASES,
    reason="licensed full-setup acceptance requires CAD_AI_RUN_ROUTER_TESTS=1 "
           "and CAD_AI_ROUTER_SETUP_PROJECTS",
)


def _quote(value):
    return json.dumps(str(value))


def _file_digest(path):
    import hashlib

    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return [size, digest.hexdigest()]


def _tree_digest(roots):
    entries = {}
    for root in roots:
        root = Path(root)
        if root.is_file():
            entries[str(root)] = _file_digest(root)
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            if path.name.endswith((".lck", ".lock")) or path.name.startswith(".lock"):
                continue
            entries[str(path)] = _file_digest(path)
    return entries


def _digest_of(entries):
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(entries):
        digest.update(path.encode())
        digest.update(json.dumps(entries[path], separators=(",", ":")).encode())
    return digest.hexdigest()


def _read_fields(path):
    fields = {}
    for line in path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
    return fields


def _wait(path, process, timeout, evidence, what):
    def detail():
        text = ""
        for name in ("stdout.log", "CDS.log"):
            source = evidence / name
            if source.exists():
                text += f"\n--- {name} ---\n" + source.read_text()[-2500:]
        return text

    deadline = time.monotonic() + timeout
    while not path.exists():
        assert process.poll() is None, f"{what}: Virtuoso exited early" + detail()
        assert time.monotonic() < deadline, f"{what}: timed out" + detail()
        time.sleep(0.1)
    return _read_fields(path)


def _scan_processes(needles):
    rows = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if all(needle in cmdline for needle in needles):
            rows.append({"pid": int(entry.name), "cmdline": cmdline.strip()})
    return rows


def _startup_source(evidence, case):
    entry = case["entry"]
    lines = [
        "procedure(acceptBool(value) if(value then \"true\" else \"false\"))",
        "procedure(acceptText(@rest items)",
        "  let((out) out=\"\" foreach(item items out=strcat(out item \"\\n\")) out))",
        "procedure(acceptWrite(path text)",
        "  let((port) port=outfile(path \"w\") when(port fprintf(port \"%s\" text) close(port))))",
        "procedure(acceptFail(message)",
        f"  acceptWrite({_quote(evidence / 'setup-error.txt')} strcat(message \"\\n\")) exit(3))",
    ]
    if entry.get("menu"):
        lines += [
            "unless(boundp('witMenuCIW) acceptFail(\"Site menus are absent\"))",
            "acceptItems=cadddr(witMenuCIW)",
            "acceptItem=car(setof(item acceptItems car(item)==\"aiAssistantAgent\"))",
            "unless(acceptItem acceptFail(\"Silicon Copilot menu entry is absent\"))",
            "evalstring(caddr(acceptItem))",
            "acceptCid1=nil",
            "prog((acceptWait)",
            "  for(acceptTick 1 300",
            "    unless(acceptCid1",
            "      foreach(acceptCid sicoBindings",
            "        when(and(sicoBindings[acceptCid]"
            " sicoBindings[acceptCid]['desktopSession]",
            "                 sicoSessionLive(acceptCid)) acceptCid1=acceptCid)))",
            "    when(acceptCid1 return(nil))",
            "    ipcSleep(1)))",
            "unless(acceptCid1 acceptFail(\"Menu entry did not start a desktop\"))",
        ]
    else:
        lines += [
            f"load({_quote(entry['loader'])})",
            "unless(isCallable('sicoShow) acceptFail(\"Site loader is incomplete\"))",
            "acceptSetupBinding=makeTable(\"acceptSetup\" nil)",
            "acceptSetupBinding['entry]=\"menu\"",
            "acceptCid1=sicoShow(nil acceptSetupBinding t)",
            "unless(acceptCid1 acceptFail(\"Loader entry did not start a desktop\"))",
        ]
    lines += [
        "acceptBinding2=makeTable(\"acceptSetup\" nil)"
        " acceptBinding2['entry]=\"router-acceptance\"",
        "acceptCid2=sicoShow(nil acceptBinding2 t)",
        'unless(acceptCid2 acceptFail("Second desktop did not start"))',
        'unless(and(sicoInstanceBridge!=acceptCid1 sicoInstanceBridge!=acceptCid2'
        " sicoBindings[acceptCid1]['generation]==sicoBindings[acceptCid2]['generation])"
        ' acceptFail("Desktop pair does not share one bridge"))',
        f"acceptWrite({_quote(evidence / 'instance.json')} acceptText(",
        '  strcat("instance_id=" sicoBindings[sicoInstanceBridge][\'instanceId])',
        '  strcat("generation=" sicoBindings[sicoInstanceBridge][\'generation])',
        '  strcat("desktop1=" sprintf(nil "%L" acceptCid1))',
        '  strcat("desktop2=" sprintf(nil "%L" acceptCid2))',
        "  strcat(\"loader=\" if(isCallable('sicoLoaderRevision)"
        " then sicoLoaderRevision() else \"context-runtime\"))",
        "  strcat(\"entry=\" " + _quote("menu" if entry.get("menu") else "loader") + ")",
        '  strcat("ai_agent_root=" or(sicoRoot ""))',
        '  strcat("sico_root=" or(getShellEnvVar("SICO_ROOT") ""))',
        '  strcat("cad_agent_root=" or(getShellEnvVar("CAD_AGENT_ROOT") ""))',
        '  strcat("cad_home=" or(getShellEnvVar("CAD_HOME") ""))',
        '  strcat("workdir=" aiWorkingDirectory())',
        '  strcat("temp=" or(getShellEnvVar("CAD_TEMP_DIR") ""))))',
        "procedure(acceptFinish()",
        "  let((guard counter)",
        "    when(sicoBindings[acceptCid2] sicoStopDesktop(acceptCid2))",
        "    when(sicoBindings[acceptCid1] sicoStopDesktop(acceptCid1))",
        "    guard=sicoInstanceBridge",
        "    when(guard",
        "      sicoDisconnect(guard)",
        "      for(counter 1 10 when(ipcIsAliveProcess(guard) ipcSleep(1))))",
        "    exit()))",
        "procedure(acceptPoll()",
        f"  if(isFile({_quote(evidence / 'done')}) then acceptFinish())",
        '  hiRegTimer("acceptPoll()" 1))',
        'hiRegTimer("acceptPoll()" 1)',
    ]
    return "\n".join(lines) + "\n"


def _launcher_source(evidence, case):
    profile = Path(case["setup_profile"])
    return "\n".join([
        "#!/bin/bash",
        "set -u",
        f"source {profile} >{evidence / 'setup-source.log'} 2>&1",
        "source_status=$?",
        "if [ $source_status -ne 0 ]; then exit $source_status; fi",
        f"export CAD_TEMP_DIR={evidence / 'scratch'}",
        f"export TMPDIR={evidence / 'scratch'} TMP={evidence / 'scratch'}",
        f"export TEMP={evidence / 'scratch'} SQLITE_TMPDIR={evidence / 'scratch'}",
        f"cd {case['project_dir']}",
        'exec "$@"',
        "",
    ])


def _run_project(case, tmp_path):
    evidence = tmp_path / "acceptance"
    evidence.mkdir()
    (evidence / "scratch").mkdir()
    startup = evidence / "startup.il"
    startup.write_text(_startup_source(evidence, case))
    launcher = evidence / "launch.sh"
    launcher.write_text(_launcher_source(evidence, case))
    launcher.chmod(0o700)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH",
                       "CDS_LIB", "CDS_INIT_FILE"}
        and not key.startswith(("QT_", "SICO_", "CAD_AI_", "CAD_AGENT_"))
    }
    env["PWD"] = str(case["project_dir"])
    command = [
        str(launcher), str(VIRTUOSO), "-cdslib", str(Path(case["project_dir"]) / "cds.lib"),
        "-restore", str(startup), "-log", str(evidence / "CDS.log"),
    ]
    if not case["graphical"]:
        command.append("-nographE")
    with (evidence / "stdout.log").open("w") as output:
        process = subprocess.Popen(command, cwd=case["project_dir"], env=env,
                                   stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT)
    try:
        fields = _wait(evidence / "instance.json", process, 240, evidence, "full setup startup")
        assert re.fullmatch(r"v[1-9][0-9]*", fields["instance_id"]), fields
        assert fields["entry"] == ("menu" if case["entry"].get("menu") else "loader"), fields
        resolved_root = Path(fields["ai_agent_root"]).resolve()
        assert resolved_root == Path("/software/cad/tools/ai").resolve(), fields
        assert fields["desktop1"] != fields["desktop2"], fields
        assert fields["temp"] == str(evidence / "scratch"), fields
        launch_dir = Path(case["project_dir"])
        context = BoundContext(fields["instance_id"], fields["generation"], "bound",
                               {"cwd": str(launch_dir), "valid": True})
        with BridgeClient.discover(launch_dir, context) as client:
            bound = client.context(fields["instance_id"], "bound")
            deadline = time.monotonic() + 60
            while len(client.bridge_snapshot()["sessions"]) != 2:
                assert time.monotonic() < deadline, client.bridge_snapshot()
                time.sleep(0.1)
            snapshot = client.bridge_snapshot()
            with BridgeClient.discover(launch_dir, context) as second, \
                    skill_call_context(session_id="setup-read"):
                second.context(fields["instance_id"])
                second.register_session("setup-read", bound)
                assert second.get_context(bound)["valid"]
                second.release_session("setup-read")
            with BridgeClient.discover(launch_dir, context) as first, \
                    skill_call_context(session_id="setup-route"):
                first.context(fields["instance_id"])
                first.register_session("setup-route", bound)
                result = first.assistant_call(
                    bound, "eval_skill", 'progn(ipcSleep(1) "SETUP_ROUTE_OK")')
                assert result["ok"] is True and result["value"] == "SETUP_ROUTE_OK", result
                first.release_session("setup-route")
            (evidence / "routing.json").write_text(json.dumps({
                "snapshot": snapshot, "route_result": result,
            }, indent=2))
        processes = _scan_processes(["desktop-worker", str(case["project_dir"])])
        assert len(processes) == 2, processes
        assert all("sico" in row["cmdline"] for row in processes), processes
        (evidence / "setup-entry.json").write_text(json.dumps({
            "fields": fields, "desktop_processes": processes,
        }, indent=2))
        (evidence / "done").touch()
        assert process.wait(timeout=60) == 0, (evidence / "stdout.log").read_text()[-3000:]
        stdout_text = (evidence / "stdout.log").read_text()
        assert "*Error*" not in stdout_text, stdout_text[-3000:]
        assert "Traceback" not in stdout_text, stdout_text[-3000:]
        runtime = Path(case["project_dir"]) / ".cad/ai/agent"
        assert runtime.is_dir(), runtime
    finally:
        if process.poll() is None:
            (evidence / "done").touch()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


@pytest.mark.parametrize("case", CASES, ids=[item["label"] for item in CASES])
def test_full_setup_startup(case, tmp_path):
    before = _tree_digest(case["fingerprint"])
    _run_project(case, tmp_path)
    after = _tree_digest(case["fingerprint"])
    changed = sorted(path for path in set(before) | set(after)
                     if before.get(path) != after.get(path))
    assert not changed, changed[:20]
    (tmp_path / "acceptance" / "fingerprint.json").write_text(json.dumps({
        "roots": [str(path) for path in case["fingerprint"]],
        "files": len(before), "digest": _digest_of(before), "changed": changed,
    }, indent=2))
