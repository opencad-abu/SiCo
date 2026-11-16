"""Regression coverage for the process-scoped Silicon Copilot SKILL loader."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

CAD = Path(__file__).absolute().parents[3]
DB = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/dbAccess")
VIRTUOSO = DB.with_name("virtuoso")


@pytest.mark.skipif(
    not VIRTUOSO.is_file() or os.environ.get("SICO_AI_RUN_BRIDGE_TESTS") != "1",
    reason="licensed isolated bridge acceptance requires SICO_AI_RUN_BRIDGE_TESTS=1",
)
def test_instance_bridge_starts_two_desktops_in_isolated_virtuoso(tmp_path):
    import time

    from sico.core.contracts import BoundContext
    from sico.transport.bridge_client import BridgeClient

    receipt = tmp_path / "bridge-identity.json"
    source = tmp_path / "bridge-start.il"
    source.write_text("\n".join([
        f'load({json.dumps(str(CAD / "sico/skill++/SICO.ils"))})',
        'bridgeTestBinding=makeTable("bridgeTestBinding" nil)',
        'bridgeTestBinding[\'entry]="bridge-acceptance"',
        'bridgeTestFirst=sicoShow(nil bridgeTestBinding t)',
        'bridgeTestBinding2=makeTable("bridgeTestBinding2" nil)',
        'bridgeTestBinding2[\'entry]="bridge-acceptance"',
        'bridgeTestSecond=sicoShow(nil bridgeTestBinding2 t)',
        'unless(and(bridgeTestFirst bridgeTestSecond bridgeTestFirst!=bridgeTestSecond'
        ' sicoInstanceBridge!=bridgeTestFirst sicoInstanceBridge!=bridgeTestSecond'
        ' sicoBindings[bridgeTestFirst][\'generation]==sicoBindings[bridgeTestSecond][\'generation]'
        ' eq(sicoBindings[bridgeTestFirst][\'targets] sicoBindings[sicoInstanceBridge][\'targets])'
        ' eq(sicoBindings[bridgeTestSecond][\'targets] sicoBindings[sicoInstanceBridge][\'targets]))'
        ' error("Instance bridge ownership mismatch"))',
        f'bridgeTestReceipt=outfile({json.dumps(str(receipt))})',
        'fprintf(bridgeTestReceipt "{\\"instance_id\\":\\"%s\\",\\"generation\\":\\"%s\\"}"'
        ' sicoBindings[sicoInstanceBridge][\'instanceId]'
        ' sicoBindings[sicoInstanceBridge][\'generation])',
        'close(bridgeTestReceipt)',
        'procedure(bridgeTestExit() sicoStopDesktop(bridgeTestFirst)'
        ' sicoStopDesktop(bridgeTestSecond) sicoDisconnect(sicoInstanceBridge) exit())',
        f'procedure(bridgeTestPoll() if(isFile({json.dumps(str(tmp_path / "done"))})'
        ' then bridgeTestExit() else hiRegTimer("bridgeTestPoll()" 1)))',
        'hiRegTimer("bridgeTestPoll()" 1)',
    ]) + "\n")
    (tmp_path / "cds.lib").write_text("")
    env = {key: value for key, value in os.environ.items()
           if key not in {"LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH"}
           and not key.startswith("QT_")
           and not key.startswith("SICO_")}
    env.update(SICO_HOME=str(CAD.parent), SICO_PYTHON=sys.executable,
               SICO_SKILL_TIMEOUT="10")
    with (tmp_path / "stdout.log").open("w") as output:
        process = subprocess.Popen([str(VIRTUOSO), "-nographE", "-nocdsinit", "-cdslib",
                                    str(tmp_path / "cds.lib"), "-log", str(tmp_path / "CDS.log"),
                                   "-restore", str(source)], cwd=tmp_path, env=env,
                                   stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 30
        while not receipt.exists():
            assert process.poll() is None and time.monotonic() < deadline, (tmp_path / "stdout.log").read_text()
            time.sleep(.1)
        identity = json.loads(receipt.read_text())
        context = BoundContext(identity["instance_id"], identity["generation"], "bound",
                               {"cwd": str(tmp_path), "valid": True})
        with BridgeClient.discover(tmp_path, context) as client:
            deadline = time.monotonic() + 30
            while len(client.bridge_snapshot()["sessions"]) != 2:
                assert time.monotonic() < deadline, (tmp_path / "stdout.log").read_text()
                time.sleep(.1)
            snapshot = client.bridge_snapshot()
            assert len(snapshot["instances"]) == 1
            targets = {row["target_id"] for row in snapshot["sessions"].values()}
            assert len(targets) == 2 and "bound" not in targets
            for index, target in enumerate(targets):
                current = client.context(identity["instance_id"], target)
                session = "probe-" + str(index)
                client.register_session(session, current)
                from sico.transport.broker import skill_call_context
                with skill_call_context(session_id=session):
                    assert client.get_context(current)["valid"]
                client.release_session(session)
            (tmp_path / "bridge-acceptance.json").write_text(json.dumps(snapshot, indent=2))
        (tmp_path / "done").touch()
        assert process.wait(timeout=15) == 0
        assert "*Error*" not in (tmp_path / "stdout.log").read_text()
        assert "Traceback" not in (tmp_path / "stdout.log").read_text()
    finally:
        if process.poll() is None:
            (tmp_path / "done").touch()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.skipif(
    not VIRTUOSO.is_file() or os.environ.get("SICO_AI_RUN_BRIDGE_TESTS") != "1",
    reason="licensed isolated bridge acceptance requires SICO_AI_RUN_BRIDGE_TESTS=1",
)
def test_virtuoso_exit_closes_and_seals_the_instance_bridge(tmp_path):
    import time

    receipt = tmp_path / "bridge-ready.txt"
    source = tmp_path / "exit-start.il"
    source.write_text("\n".join([
        f'load({json.dumps(str(CAD / "sico/skill++/SICO.ils"))})',
        'exitBinding=makeTable("exitBinding" nil)',
        'exitBinding[\'entry]="exit-acceptance"',
        'exitBinding[\'cwd]=getWorkingDir()',
        'exitCid=sicoEnsureBridge(exitBinding)',
        'unless(exitCid error("Instance bridge unavailable"))',
        f'exitReceipt=outfile({json.dumps(str(receipt))})',
        'fprintf(exitReceipt "ready\\n")',
        'close(exitReceipt)',
        'hiRegTimer("exit(0)" 5)',
    ]) + "\n")
    (tmp_path / "cds.lib").write_text("")
    env = {key: value for key, value in os.environ.items()
           if key not in {"LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH"}
           and not key.startswith("QT_")
           and not key.startswith("SICO_")}
    env.update(SICO_HOME=str(CAD.parent), SICO_PYTHON=sys.executable,
               SICO_SKILL_TIMEOUT="10")
    with (tmp_path / "stdout.log").open("w") as output:
        process = subprocess.Popen([str(VIRTUOSO), "-nographE", "-nocdsinit", "-cdslib",
                                    str(tmp_path / "cds.lib"), "-log", str(tmp_path / "CDS.log"),
                                    "-restore", str(source)], cwd=tmp_path, env=env,
                                   stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 30
        while not receipt.exists():
            assert process.poll() is None and time.monotonic() < deadline, (
                tmp_path / "stdout.log").read_text()
            time.sleep(.1)
        assert receipt.read_text().strip() == "ready"
        deadline = time.monotonic() + 10
        bridges = tmp_path / ".sico/ai/agent/bridges"
        while not list(bridges.glob("*.json")):
            assert time.monotonic() < deadline, (tmp_path / "stdout.log").read_text()
            time.sleep(.1)
        assert process.wait(timeout=30) == 0, (tmp_path / "stdout.log").read_text()
        deadline = time.monotonic() + 5
        while not list(bridges.glob("*.retired.json")):
            assert time.monotonic() < deadline, (tmp_path / "stdout.log").read_text()
            time.sleep(.1)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(not DB.is_file(), reason="IC231 dbAccess is not installed")
def test_agent_loader_loads_inspection_once_and_preserves_state(tmp_path):
    loader = str(CAD / "sico/skill++/SICO.ils")
    lines = [
        f'load("{loader}")',
        'printf("BOUND loader=%L inspect=%L ctx=%L ade=%L menus=%L quick=%L rmb=%L sessions=%L launch=%L show=%L\\n" boundp(\'sicoLoaderVersion) boundp(\'aiInspectVersion) isCallable(\'sicoContextRevision) isCallable(\'sicoAdeRevision) isCallable(\'sicoMenusRevision) isCallable(\'sicoQuickRevision) isCallable(\'sicoRmbRevision) isCallable(\'sicoSessionsRevision) isCallable(\'sicoLauncherRevision) isCallable(\'sicoShow))',
        'unless(and(isCallable(\'aiInspectLayout)'
        ' isCallable(\'aiInspectSchematic)'
        ' isCallable(\'aiCdfInspect) isCallable(\'aiCdfPut) isCallable(\'aiCdfRead)'
        ' isCallable(\'sicoEntryRead) isCallable(\'aiWindowList)'
        ' isCallable(\'aiWindowInspect) isCallable(\'aiEntryContext)'
        ' sicoLoaderRevision()=="20260924.sico.loader.v6"'
        ' aiCopilotCaptureRevision()=="20261101.ai.circuit.capture.v8"'
        ' aiCdfCallbackRevision()=="20261022.ai.cdf.callbacks.v5"'
        ' aiPdkInitRevision()=="20260919.ai.pdk.init.v1"'
        ' aiCreateRevision()=="20261022.ai.create.v12"'
        ' isCallable(\'aiCrTextFinish) isCallable(\'aiCrExportSvg)'
        ' aiCdfProbeRevision()=="20260924.ai.cdf.probe.v4"'
        ' isCallable(\'aiCdfUpdatePrepare) isCallable(\'aiCdfUpdateApply)'
        ' sicoContextRevision()=="20261101.sico.context.v6"'
        ' sicoAdeRevision()=="20260921.sico.ade.v1"'
        ' sicoMenusRevision()=="20261020.sico.menus.v2"'
        ' sicoQuickRevision()=="20261023.sico.quick.v11"'
        ' sicoRmbRevision()=="20260921.sico.rmb.v2"'
        ' sicoSessionsRevision()=="20260921.sico.sessions.v1"'
        ' isCallable(\'sicoPlaceCiw) isCallable(\'sicoPlaceLaunchedCiw)'
        ' sicoLauncherRevision()=="20261024.sico.launcher.v8")'
        ' error("Agent loader revisions or inspection helpers missing"))',
        'loaderNoWindow=makeTable("loaderNoWindow" nil)',
        'loaderSnapshot=sicoContextJson(loaderNoWindow)',
        'when(rexMatchp("read_ade_setup" loaderSnapshot)'
        ' error("ADE reads advertised without ADE binding"))',
        'unless(and(!loaderNoWindow[\'window]'
        ' rexMatchp("list_windows" loaderSnapshot)'
        ' rexMatchp("inspect_window" loaderSnapshot)'
        ' rexMatchp("get_entry_context" loaderSnapshot))'
        ' error("Installed context tools require a captured window"))',
        'loaderState="preserved"',
        'sicoBindings["state-probe"]=list("preserved")',
        'sicoQuickWindows["state-probe"]=list("preserved")',
        'sicoLayoutMenus["state-probe"]=list("preserved")',
        f'load("{loader}")',
        'unless(and(loaderState=="preserved"'
        ' sicoBindings["state-probe"]==list("preserved")'
        ' sicoQuickWindows["state-probe"]==list("preserved")'
        ' sicoLayoutMenus["state-probe"]==list("preserved"))'
        ' error("Agent runtime state was reset"))',
        'printf("AGENT_LOADER_PASS\\n") exit()',
    ]
    source = tmp_path / "loader.il"
    source.write_text("\n".join(lines) + "\n")
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "SICO_ROOT"}
    }
    result = subprocess.run(
        [str(DB), "-load", str(source)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    combined = result.stdout + result.stderr
    assert "AGENT_LOADER_PASS" in combined, combined
    assert "*Error*" not in combined, combined
    assert "redefined" not in combined.lower(), combined


@pytest.mark.parametrize("legacy_slots", [False, True], ids=["fresh", "legacy-slots"])
@pytest.mark.skipif(not DB.is_file(), reason="IC231 dbAccess is not installed")
def test_agent_loader_reuses_process_after_skillpp_upgrade(tmp_path, legacy_slots):
    loader = json.dumps(str(CAD / "sico/skill++/SICO.ils"))
    legacy = tmp_path / "legacy.ils"
    legacy.write_text(
        'sicoLoaderVersion="20260921.sico.loader.v1"\n'
        'set(\'sicoLoaderVersion "20260921.sico.loader.v1")\n'
    )
    lines = [
        "loaderRootCalls=0",
        "procedure(witMenuToolsRoot() loaderRootCalls=loaderRootCalls+1 "
        + json.dumps(str(CAD)) + ")",
    ]
    if legacy_slots:
        lines.append(f"load({json.dumps(str(legacy))})")
    lines.extend([
        f"load({loader})",
        'unless(loaderRootCalls==0 error("SiCo must resolve its own fixed installation anchor"))',
        'unless(sicoLoaderVersion==sicoLoaderRevision()'
        ' error("Loader did not publish its completed revision"))',
        "loaderBindings=sicoBindings loaderQuickWindows=sicoQuickWindows",
        "loaderLayoutMenus=sicoLayoutMenus loaderWindowSessions=sicoWindowSessions",
        'sicoBindings["state-probe"]=list("preserved")',
        'sicoQuickWindows["state-probe"]=list("preserved")',
        'sicoLayoutMenus["state-probe"]=list("preserved")',
        'sicoWindowSessions["state-probe"]="session-preserved"',
        'printf("AGENT_REPEAT_BEGIN\\n")',
        f"for(loaderRepeat 1 3 load({loader}))",
        'printf("AGENT_REPEAT_END\\n")',
        'unless(loaderRootCalls==0 error("Repeated load entered the loader body"))',
        'unless(and(eq(loaderBindings sicoBindings)'
        ' eq(loaderQuickWindows sicoQuickWindows) eq(loaderLayoutMenus sicoLayoutMenus)'
        ' eq(loaderWindowSessions sicoWindowSessions)'
        ' sicoBindings["state-probe"]==list("preserved")'
        ' sicoQuickWindows["state-probe"]==list("preserved")'
        ' sicoLayoutMenus["state-probe"]==list("preserved")'
        ' sicoWindowSessions["state-probe"]=="session-preserved")'
        ' error("Agent runtime state was reset"))',
        'printf("AGENT_UPGRADE_PASS\\n") exit()',
    ])
    source = tmp_path / "upgrade.il"
    source.write_text("\n".join(lines) + "\n")
    env = {key: value for key, value in os.environ.items()
           if key not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "SICO_ROOT"}}
    result = subprocess.run(
        [str(DB), "-load", str(source)], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=20,
    )
    combined = result.stdout + result.stderr
    assert "AGENT_UPGRADE_PASS" in combined, combined
    assert "*Error*" not in combined, combined
    assert "*WARNING*" not in combined, combined
    assert "redefined" not in combined.lower(), combined


@pytest.mark.skipif(
    not VIRTUOSO.is_file() or os.environ.get("CAD_AI_RUN_LOADER_TESTS") != "1",
    reason="licensed loader acceptance requires CAD_AI_RUN_LOADER_TESTS=1",
)
def test_installed_entry_tools_work_without_captured_source(tmp_path):
    from sico.adapters.virtuoso import context_tools
    from sico.core.contracts import ToolCall
    from sico.interpreter import cad_python
    from sico.transport.broker import ContextBroker

    with ContextBroker(10, operation_timeout=20) as broker:
        connection = tmp_path / "connection.json"
        connection.write_text(json.dumps({"host": broker.address[0], "port": broker.address[1],
                                          "token": broker.token}))
        connection.chmod(0o600)
        # Virtuoso's embedded /bin/python is not a complete runtime. Use the
        # same qualified interpreter as production relay startup.
        command = shlex.join([cad_python(), "-s", str(CAD / "sico/python/sico-entry"),
                              "relay", "--connection-file", str(connection)])
        source, stop = tmp_path / "entry.il", tmp_path / "stop"
        source.write_text("\n".join([
            "load(" + json.dumps(str(CAD / "sico/skill++/SICO.ils")) + ")",
            'entryTestBinding=makeTable("entryTestBinding" nil)',
            "entryTestCid=sicoConnect(" + json.dumps(command) +
            ' "entry-test" "generation" nil entryTestBinding)',
            "entryTestTicks=0",
            "procedure(entryTestExit() entryTestTicks=entryTestTicks+1 if(or(isFile(" +
            json.dumps(str(stop)) + ') entryTestTicks>600) then exit() else hiRegTimer("entryTestExit()" 1)))',
            'hiRegTimer("entryTestExit()" 1)',
        ]) + "\n")
        (tmp_path / "cds.lib").write_text("")
        env = {key: value for key, value in os.environ.items()
               if key not in {"LD_PRELOAD", "PYTHONPATH", "PYTHONHOME"}}
        env.update(SICO_HOME=str(CAD.parent), SICO_SKILL_TIMEOUT="10")
        with (tmp_path / "stdout.log").open("w") as output:
            process = subprocess.Popen([str(VIRTUOSO), "-nographE", "-nocdsinit", "-cdslib",
                str(tmp_path / "cds.lib"), "-log", str(tmp_path / "CDS.log"), "-restore", str(source)],
                cwd=tmp_path, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            context = broker.context("entry-test", timeout=30)
            assert context.snapshot["window_ref"] == ""
            assert {"list_windows", "inspect_window", "get_entry_context"} <= set(
                context.snapshot["capabilities"])
            registry = context_tools(broker)

            def read(name, args=None):
                return registry.execute(ToolCall("entry", name, args or {}), context)

            uncaptured = read("get_entry_context")
            assert uncaptured.status == "ok"
            assert uncaptured.data["captured"] is False
            listed = read("list_windows")
            assert listed.status == "ok", listed
            ciw = next(row for row in listed.data["windows"] if row["kind"] == "ciw")
            args = {"window_ref": ciw["window_ref"]}
            assert read("inspect_window", args).status == "ok"
            explicit = read("get_entry_context", args)
            assert explicit.status == "ok" and explicit.data["source"]["kind"] == "ciw"
            assert read("get_entry_context", {"window_ref": "wctx_missing_999"}).status == "stale_context"
            # Losing the original source must not block enumeration or an
            # explicitly referenced live window in the same project.
            broker.assistant_call(context, "eval_skill",
                                  'entryTestBinding[\'cellJson]="expired"')
            assert read("get_entry_context").status == "stale_context"
            assert read("list_windows").status == "ok"
            assert read("get_entry_context", args).status == "ok"
        finally:
            stop.touch()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
