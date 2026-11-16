"""F2 fault exercise in an isolated real Virtuoso; no user designs are opened."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from sico.core.contracts import BoundContext, NeedsReconcile
from sico.transport.bridge_client import BridgeClient
from sico.transport.broker import skill_call_context

CAD = Path(__file__).absolute().parents[3]
IC = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/virtuoso")


@pytest.mark.skipif(os.environ.get("CAD_AI_RUN_ROUTER_TESTS") != "1" or not IC.is_file(),
                    reason="licensed isolated F2 acceptance requires CAD_AI_RUN_ROUTER_TESTS=1")
@pytest.mark.parametrize("graphical", [False, True], ids=["nograph", "foreground"])
def test_real_skill_delay_queue_unknown_late_receipt_and_generation_retirement(tmp_path, graphical):
    if graphical and not os.environ.get("DISPLAY"):
        pytest.skip("Foreground acceptance requires DISPLAY")
    identity_path, done = tmp_path / "identity.json", tmp_path / "done"
    startup = tmp_path / "startup.il"
    startup.write_text("\n".join([
        f'load({json.dumps(str(CAD / "sico/skill++/SICO.ils"))})',
        'f2Source=makeTable("f2Source" nil) f2Source[\'entry]="f2-acceptance"',
        'f2Bridge=sicoEnsureBridge(f2Source)',
        f'f2Identity=outfile({json.dumps(str(identity_path))})',
        'fprintf(f2Identity "{\\"instance_id\\":\\"%s\\",\\"generation\\":\\"%s\\",\\"snapshot\\":%s}"'
        ' sicoBindings[f2Bridge][\'instanceId] sicoBindings[f2Bridge][\'generation]'
        ' sicoContextJson(sicoBindings[f2Bridge])) close(f2Identity)',
        'procedure(f2Restart() sicoReconnectBridge() f2Bridge=sicoEnsureBridge(f2Source)'
        f' f2Next=outfile({json.dumps(str(tmp_path / "next.json"))})'
        ' fprintf(f2Next "{\\"instance_id\\":\\"%s\\",\\"generation\\":\\"%s\\",\\"snapshot\\":%s}"'
        ' sicoBindings[f2Bridge][\'instanceId] sicoBindings[f2Bridge][\'generation]'
        ' sicoContextJson(sicoBindings[f2Bridge])) close(f2Next))',
        'procedure(f2Exit() sicoDisconnect(f2Bridge) exit())',
        f'procedure(f2Poll() if(isFile({json.dumps(str(done))}) then f2Exit()'
        f' else when(and(isFile({json.dumps(str(tmp_path / "reconnect"))})'
        f' !isFile({json.dumps(str(tmp_path / "next.json"))})) f2Restart())'
        ' hiRegTimer("f2Poll()" 1))) hiRegTimer("f2Poll()" 1)',
    ]) + "\n")
    (tmp_path / "cds.lib").write_text("")
    env = {key: value for key, value in os.environ.items()
           if key not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH"}
           and not key.startswith(("QT_", "SICO_"))}
    env.update(SICO_HOME=str(CAD.parent), CAD_PYTHON=sys.executable, SICO_SKILL_TIMEOUT="1")
    command = [str(IC), "-nocdsinit", "-cdslib", str(tmp_path / "cds.lib"),
               "-restore", str(startup), "-log", str(tmp_path / "CDS.log")]
    if not graphical:
        command.append("-nographE")
    with (tmp_path / "stdout.log").open("w") as output:
        process = subprocess.Popen(command, cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 30
        while not identity_path.exists():
            assert process.poll() is None and time.monotonic() < deadline, (tmp_path / "stdout.log").read_text()
            time.sleep(.1)
        record = json.loads(identity_path.read_text())
        context = BoundContext(record["instance_id"], record["generation"], "bound", record["snapshot"])
        with BridgeClient.discover(tmp_path, context) as first, BridgeClient.discover(tmp_path, context) as second, ThreadPoolExecutor(2) as pool:
            first.context(context.instance_id)
            first.register_session("f2-first", context)
            second.register_session("f2-second", context)
            running, queued = threading.Event(), threading.Event()
            records = []

            def execute():
                def progress(row):
                    records.append(row)
                    if row["state"] == "running":
                        running.set()
                with skill_call_context(session_id="f2-first", progress=progress):
                    return first.assistant_call(context, "eval_skill",
                        'progn(unless(and(sicoBindings[f2Bridge][\'skillActive]'
                        ' !sicoReconnectBridge()) error("Active SKILL was not protected"))'
                        ' ipcSleep(4) "F2_LATE_RESULT")')

            def waiting():
                def progress(row):
                    records.append(row)
                    if row["state"] == "queued":
                        queued.set()
                with skill_call_context(session_id="f2-second", progress=progress):
                    return second.get_context(context)

            active = pool.submit(execute)
            assert running.wait(5)
            started = time.monotonic()
            next_call = pool.submit(waiting)
            assert queued.wait(.8)
            with pytest.raises(NeedsReconcile, match="skill_timeout"):
                active.result(3)
            with pytest.raises(NeedsReconcile, match="skill_blocked_unknown"):
                next_call.result(2)
            blocked = time.monotonic() - started
            assert blocked < 2.5
            first_id = next(row["request_id"] for row in records if row.get("session_id") == "f2-first")
            second_id = next(row["request_id"] for row in records if row.get("session_id") == "f2-second")
            during_timeout = first.request_receipt(context, first_id, session_id="f2-first")
            assert during_timeout["state"] == "timed_out_unknown"
            assert during_timeout["skill_stage"]["event"] == "skill.evaluation_started"
            assert "reply" not in during_timeout
            first.release_session("f2-first")
            deadline = time.monotonic() + 8
            while second.router_snapshot(context)["unknown"]:
                assert time.monotonic() < deadline
                time.sleep(.05)
            late = first.request_receipt(context, first_id, session_id="f2-first")
            blocked_receipt = second.request_receipt(context, second_id, session_id="f2-second")
            assert late["state"] == "late_reply" and "F2_LATE_RESULT" in json.dumps(late["reply"])
            assert blocked_receipt["state"] == "blocked_unknown" and "reply" not in blocked_receipt
            assert second.get_context(context)["valid"]
            (tmp_path / "reconnect").touch()
            deadline = time.monotonic() + 10
            while not (tmp_path / "next.json").exists():
                assert time.monotonic() < deadline
                time.sleep(.05)
            next_record = json.loads((tmp_path / "next.json").read_text())
            assert next_record["generation"] != context.generation
            assert next_record["instance_id"] == context.instance_id
            next_context = BoundContext(context.instance_id, next_record["generation"], "bound", next_record["snapshot"])
            with BridgeClient.discover(tmp_path, next_context) as replacement:
                replacement.context(context.instance_id)
                assert replacement.bridge_snapshot()["sessions"] == {}
                assert replacement.router_snapshot(next_context)["unknown"] is None
                replacement.register_session("f2-new", next_context)
                assert replacement.get_context(next_context)["valid"]
            with pytest.raises(NeedsReconcile):
                second.get_context(context)
            (tmp_path / "acceptance.json").write_text(json.dumps(dict(blocked_seconds=blocked,
                late=late, blocked=blocked_receipt, records=records, during_timeout=during_timeout,
                next_generation=next_context.generation), indent=2))
        done.touch()
        assert process.wait(timeout=15) == 0
        archived = BridgeClient.archived_receipt(tmp_path, context, first_id, session_id="f2-first")
        assert archived["state"] == "late_reply" and not archived["automatic_resume_allowed"]
        from sico.transport.router_archive import maintain_router_logs

        maintenance = maintain_router_logs(tmp_path, apply=True, retention_days=0)
        assert not maintenance['errors'], maintenance
        assert len(maintenance['generations']) == 2
        assert all(row['status'] == 'archived' for row in maintenance['generations']), maintenance
        assert BridgeClient.archived_receipt(tmp_path, context, first_id,
                                             session_id="f2-first") == archived
        (tmp_path / 'archive-acceptance.json').write_text(json.dumps(maintenance, indent=2))
        assert "Traceback" not in (tmp_path / "stdout.log").read_text()
    finally:
        if process.poll() is None:
            done.touch()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
