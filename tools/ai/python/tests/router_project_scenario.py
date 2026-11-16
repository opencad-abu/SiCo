"""One native routing acceptance episode and its owned process cleanup."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from router_project_config import CAD, CAD_PYTHON, FIFO_SLEEP, LATE_SLEEP, SKILL_TIMEOUT, VIRTUOSO
from router_project_files import _receipt_core as _receipt_core
from router_project_files import _tree_digest as _tree_digest
from router_project_processes import _ancestry as _ancestry
from router_project_processes import _progress_log as _progress_log
from router_project_processes import _scan_processes as _scan_processes
from router_project_processes import _wait as _wait
from router_project_startup import _startup_source as _startup_source

from sico.core.contracts import BoundContext, NeedsReconcile
from sico.transport.bridge_client import BridgeClient
from sico.transport.broker import skill_call_context
from sico.transport.router_archive import maintain_router_logs


def _run_project(project, tmp_path, episode):
    evidence = tmp_path / episode
    evidence.mkdir()
    launch = evidence
    startup = evidence / "startup.il"
    startup.write_text(_startup_source(evidence, project))
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH", "CDS_LIB"}
        and not key.startswith(("QT_", "SICO_", "CAD_AI_"))
    }
    temp = evidence / "temp"
    temp.mkdir()
    agent_root = str(project.get("agent_root", CAD / "ai"))
    env.update(
        PWD=str(launch),
        PROJ_HOME=str(project["project_home"]),
        SICO_ROOT=agent_root,
        CAD_AGENT_ROOT=agent_root,
        CAD_PYTHON=str(project.get("cad_python", CAD_PYTHON)),
        SICO_SKILL_TIMEOUT=str(SKILL_TIMEOUT),
        CAD_AGENT_SKILL_TIMEOUT=str(SKILL_TIMEOUT),
        CAD_TEMP_DIR=str(temp),
        TMPDIR=str(temp),
        TMP=str(temp),
        TEMP=str(temp),
        SQLITE_TMPDIR=str(temp),
    )
    if project.get("cad_home"):
        env["CAD_HOME"] = str(project["cad_home"])
    if project.get("context"):
        env["CAD_AI_CONTEXT"] = str(project["context"])
    command = [
        str(VIRTUOSO), "-nocdsinit", "-cdslib", str(project["cdslib"]),
        "-restore", str(startup), "-log", str(evidence / "CDS.log"),
    ]
    if not project["graphical"]:
        command.append("-nographE")
    if project.get("bwrap"):
        prefix = ["bwrap", "--ro-bind", "/", "/", "--bind", "/tmp", "/tmp",
                  "--dev-bind", "/dev", "/dev", "--proc", "/proc"]
        for hidden in project.get("hidden_tmpfs", []):
            prefix += ["--tmpfs", str(hidden)]
        command = prefix + command
    with (evidence / "stdout.log").open("w") as output:
        process = subprocess.Popen(
            command, cwd=str(launch), env=env, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT,
        )
    try:
        fields = _wait(evidence / "instance.json", process, 180, evidence, "instance startup")
        assert fields["label"] == project["label"]
        assert re.fullmatch(r"v[1-9][0-9]*", fields["instance_id"]), fields
        assert fields["lib"] == project["lib"]
        resolved = Path(fields["lib_path"]).resolve()
        expected = (Path(project["project_home"]).resolve() / "xh" / project["lib"]).resolve()
        assert resolved == expected, (resolved, expected)
        assert fields["technology"], fields
        if project.get("context"):
            assert fields["loader"] in {"context-runtime"} or re.fullmatch(
                r"\d{8}\.ai\.agent\.loader\.v\d+", fields["loader"]), fields
        else:
            assert re.fullmatch(r"\d{8}\.ai\.agent\.loader\.v\d+", fields["loader"]), fields
        if project.get("agent_root"):
            assert Path(fields["ai_agent_root"]).resolve() == Path(
                project["agent_root"]).resolve(), fields
        if project.get("context"):
            assert Path(fields["context"]).resolve() == Path(project["context"]).resolve(), fields
        context = BoundContext(fields["instance_id"], fields["generation"], "bound",
                               {"cwd": str(launch), "valid": True})
        old_sessions = set()
        with BridgeClient.discover(launch, context) as client:
            bound = client.context(fields["instance_id"], "bound")
            assert bound.snapshot.get("cwd") == str(launch), bound.record()
            deadline = time.monotonic() + 60
            while True:
                snapshot = client.bridge_snapshot()
                sessions = snapshot["sessions"]
                if len(sessions) == 2 and all(len(row["targets"]) == 1
                                              for row in sessions.values()):
                    break
                assert time.monotonic() < deadline, snapshot
                time.sleep(0.1)
            desktops = {
                session_id: row["targets"][0]["target_id"] for session_id, row in sessions.items()
            }
            assert len(snapshot["instances"]) == 1, snapshot
            assert snapshot["instances"][0]["instance_id"] == fields["instance_id"]
            assert snapshot["instances"][0]["generation"] == fields["generation"]
            assert snapshot["instances"][0]["router_id"], snapshot
            assert len(set(desktops.values())) == 2 and "bound" not in desktops.values(), desktops
            old_sessions |= set(desktops)
            (evidence / "bridge-snapshot-live.json").write_text(json.dumps(snapshot, indent=2))

            launcher = project.get("launcher_dir")
            if launcher:
                cli = "sico.cli"
                bridge_rows = _scan_processes([cli, " bridge --launch-dir", str(launch)])
                launch_rows = _scan_processes([cli, " launch --launch-dir", str(launch)])
            else:
                bridge_rows = _scan_processes(["sico bridge --launch-dir", str(launch)])
                launch_rows = _scan_processes(["sico launch --launch-dir", str(launch)])
            assert len(bridge_rows) == 1, bridge_rows
            assert len(launch_rows) == 2, launch_rows
            desktop_rows = {}
            for session_id in desktops:
                if launcher:
                    rows = _scan_processes(["sico.cli", " desktop-worker --launch-dir",
                                            "--session", session_id, str(launch)])
                else:
                    rows = _scan_processes(
                        ["sico desktop-worker", "--session", session_id, str(launch)])
                assert len(rows) == 1, (session_id, rows)
                desktop_rows[session_id] = rows[0]
            virt_pid = int(fields["instance_id"][1:])
            assert virt_pid == process.pid or process.pid in _ancestry(virt_pid), (
                virt_pid, process.pid)
            for row in bridge_rows + launch_rows + list(desktop_rows.values()):
                assert virt_pid in _ancestry(row["pid"]), (row, virt_pid)
            if launcher:
                expected_entry = (Path(launcher) / "sico").resolve()
                expected_root = Path(launcher).resolve().parent
                for row in bridge_rows + launch_rows + list(desktop_rows.values()):
                    entry_path = row["env"].get("CAD_NATIVE_ENTRY_PATH", "")
                    tools_root = row["env"].get("CAD_RUNTIME_TOOLS", "")
                    assert entry_path and Path(entry_path).resolve() == expected_entry, row
                    assert tools_root and Path(tools_root).resolve() == expected_root, row
                    assert "/ai/python/sico" not in row["cmdline"], row
            (evidence / "process-chain.json").write_text(json.dumps(
                {"virtuoso": virt_pid, "launched": process.pid, "bridge": bridge_rows,
                 "launch": launch_rows, "desktop": desktop_rows,
                 "launcher_dir": launcher}, indent=2))

            # 1. FIFO across sessions on the shared instance router.
            progress, records = _progress_log()
            with BridgeClient.discover(launch, context) as first, \
                    BridgeClient.discover(launch, context) as second, \
                    ThreadPoolExecutor(2) as pool:
                first.context(fields["instance_id"])
                second.context(fields["instance_id"])
                first.register_session("accept-first", bound)
                second.register_session("accept-second", bound)
                running, queued = threading.Event(), threading.Event()

                def execute():
                    with skill_call_context(
                        session_id="accept-first",
                        progress=lambda row: (progress(row), running.set()
                                              if row["state"] == "running" else None),
                        ):
                        return first.assistant_call(
                            bound, "eval_skill",
                            f'progn(ipcSleep({FIFO_SLEEP}) "ROUTER_FIFO_FIRST")',
                        )

                def waiting():
                    with skill_call_context(
                        session_id="accept-second",
                        progress=lambda row: (progress(row), queued.set()
                                              if row["state"] == "queued" else None),
                    ):
                        return second.get_context(bound)

                active = pool.submit(execute)
                assert running.wait(10), records("accept-first", "running")
                started = time.monotonic()
                pending = pool.submit(waiting)
                assert queued.wait(2), records("accept-second", "queued")
                first_result = active.result(30)
                second_result = pending.result(30)
                assert first_result["ok"] is True, first_result
                assert first_result["value"] == "ROUTER_FIFO_FIRST", first_result
                assert isinstance(second_result, dict) and second_result.get("valid") is True
                first_rows = records("accept-first", "running")
                second_rows = records("accept-second", "queued")
                first_id = first_rows[0]["request_id"]
                second_id = second_rows[0]["request_id"]
                queue_position = second_rows[0].get("blocked_by")
                assert queue_position == first_id, second_rows
                first_receipt = first.request_receipt(bound, first_id, session_id="accept-first")
                second_receipt = second.request_receipt(
                    bound, second_id, session_id="accept-second")
                assert first_receipt["state"] == "completed"
                assert second_receipt["state"] == "completed"
                assert second_receipt["started_at"] >= first_receipt["finished_at"] - 0.05, (
                    first_receipt, second_receipt)
                (evidence / "fifo.json").write_text(json.dumps({
                    "first": first_receipt, "second": second_receipt,
                    "queued_after_running": queued.is_set(),
                    "elapsed_to_queue": round(time.monotonic() - started, 4),
                }, indent=2))

                # 2. Controlled timeout -> immediate blocked; late reply is quarantined.
                running.clear()
                queued.clear()

                def late_execute():
                    try:
                        with skill_call_context(
                            session_id="accept-first",
                            progress=lambda row: (progress(row), running.set()
                                                  if row["state"] == "running" else None),
                        ):
                            return first.assistant_call(
                                bound, "eval_skill",
                                f'progn(ipcSleep({LATE_SLEEP}) "ROUTER_LATE_RESULT")',
                            )
                    except NeedsReconcile as error:
                        return error

                def blocked_wait():
                    def tick(row):
                        progress(row)
                        if row["state"] == "queued":
                            queued.set()
                    try:
                        with skill_call_context(session_id="accept-second", progress=tick):
                            return second.get_context(bound)
                    except NeedsReconcile as error:
                        return error

                active = pool.submit(late_execute)
                assert running.wait(10), records("accept-first", "running")
                blocked_started = time.monotonic()
                next_call = pool.submit(blocked_wait)
                assert queued.wait(2), records("accept-second", "queued")
                timed_out = active.result(10)
                assert isinstance(timed_out, NeedsReconcile), timed_out
                assert "skill_timeout" in str(timed_out), timed_out
                blocked = next_call.result(3)
                assert isinstance(blocked, NeedsReconcile), blocked
                assert "skill_blocked_unknown" in str(blocked), blocked
                blocked_seconds = time.monotonic() - blocked_started
                assert blocked_seconds < SKILL_TIMEOUT + 2, blocked_seconds
                timeout_id = records("accept-first", "running")[-1]["request_id"]
                blocked_row = records("accept-second", "queued")[-1]
                blocked_id = blocked_row["request_id"]
                assert blocked_row.get("blocked_by") == timeout_id, blocked_row
                during = first.request_receipt(bound, timeout_id, session_id="accept-first")
                assert during["state"] == "timed_out_unknown", during
                assert during["skill_stage"]["event"] == "skill.evaluation_started", during
                assert "reply" not in during, during
                blocked_receipt = second.request_receipt(bound, blocked_id,
                                                         session_id="accept-second")
                assert blocked_receipt["state"] == "blocked_unknown", blocked_receipt
                assert "reply" not in blocked_receipt, blocked_receipt
                first.release_session("accept-first")
                deadline = time.monotonic() + 20
                while second.router_snapshot(bound)["unknown"]:
                    assert time.monotonic() < deadline
                    time.sleep(0.05)
                late = first.request_receipt(bound, timeout_id, session_id="accept-first")
                assert late["state"] == "late_reply", late
                assert late["caller_state"] == "timed_out_unknown", late
                assert "ROUTER_LATE_RESULT" in json.dumps(late["reply"]), late
                assert not late["automatic_resume_allowed"], late
                unchanged = second.request_receipt(bound, blocked_id, session_id="accept-second")
                assert _receipt_core(unchanged) == _receipt_core(blocked_receipt), (
                    unchanged, blocked_receipt)
                assert second.get_context(bound)["valid"]
                (evidence / "timeout.json").write_text(json.dumps({
                    "timeout_seconds": round(blocked_seconds, 4),
                    "during_timeout": during, "late": late, "blocked": blocked_receipt,
                }, indent=2))

                second.release_session("accept-second")
            old_sessions |= {"accept-first", "accept-second"}

            # 3. Close one desktop; the other desktop and its reads stay alive.
            (evidence / "stop_first").touch()
            _wait(evidence / "stop1.json", process, 30, evidence, "desktop stop request")
            (evidence / "check_b").touch()
            close_fields = _wait(evidence / "close1.json", process, 45, evidence, "desktop close")
            assert close_fields["first_gone"] == "true", close_fields
            assert close_fields["second_alive"] == "true", close_fields
            assert close_fields["bridge_alive"] == "true", close_fields
            assert close_fields["second_generation"] == fields["generation"], close_fields
            deadline = time.monotonic() + 30
            while True:
                snapshot = client.bridge_snapshot()
                sessions = snapshot["sessions"]
                live = {session_id for session_id in desktops
                        if sessions.get(session_id, {}).get("state") == "active"}
                if len(live) == 1:
                    break
                assert time.monotonic() < deadline, snapshot
                time.sleep(0.1)
            survivor_session = next(iter(live))
            survivor_target = desktops[survivor_session]
            closed_session = next(item for item in desktops if item != survivor_session)
            closed_target = desktops[closed_session]
            assert sessions.get(closed_session, {}).get("state") != "active", snapshot
            with pytest.raises(NeedsReconcile):
                client.context(fields["instance_id"], closed_target)
            survivor_context = client.context(fields["instance_id"], survivor_target)
            with BridgeClient.discover(launch, context) as probe, \
                    skill_call_context(session_id="accept-survivor"):
                probe.register_session("accept-survivor", survivor_context)
                assert probe.get_context(survivor_context)["valid"]
                probe.release_session("accept-survivor")
            if launcher:
                live_desktops = _scan_processes(["sico.cli", " desktop-worker --launch-dir",
                                                 str(launch)])
                survivors = _scan_processes(
                    ["sico.cli", " desktop-worker --launch-dir", "--session",
                     survivor_session, str(launch)])
            else:
                live_desktops = _scan_processes(["sico desktop-worker", str(launch)])
                survivors = _scan_processes(
                    ["sico desktop-worker", "--session", survivor_session, str(launch)])
            assert all(row["pid"] != desktop_rows[closed_session]["pid"] for row in live_desktops)
            assert len(survivors) == 1, survivors
            (evidence / "close-desktop.json").write_text(json.dumps({
                "closed_session": closed_session, "survivor_session": survivor_session,
                "closed_target": closed_target,
                "survivor_target": survivor_target, "fields": close_fields,
                "snapshot": snapshot,
            }, indent=2))

            # 4. Reconnect retires the generation without inheriting old state.
            (evidence / "reconnect").touch()
            next_fields = _wait(evidence / "next.json", process, 90, evidence,
                                "generation reconnect")
            assert next_fields["instance_id"] == fields["instance_id"], next_fields
            next_generation = next_fields["generation"]
            assert next_generation != fields["generation"], next_fields
            next_context = BoundContext(fields["instance_id"], next_generation, "bound",
                                        {"cwd": str(launch), "valid": True})
            with BridgeClient.discover(launch, next_context) as replacement, \
                    skill_call_context(session_id="accept-next"):
                replacement.context(fields["instance_id"])
                next_snapshot = replacement.bridge_snapshot()
                assert old_sessions.isdisjoint(next_snapshot["sessions"]), next_snapshot
                next_bound = replacement.context(fields["instance_id"], "bound")
                replacement.register_session("accept-next", next_bound)
                assert replacement.router_snapshot(next_bound)["unknown"] is None
                assert replacement.get_context(next_bound)["valid"]
                replacement.release_session("accept-next")
            generation_receipt = BridgeClient.archived_receipt(
                launch, context, timeout_id, session_id="accept-first")
            assert _receipt_core(generation_receipt) == _receipt_core(late), generation_receipt
            (evidence / "generation.json").write_text(json.dumps({
                "next": next_fields, "snapshot": next_snapshot,
                "archived_late_reply_core_matches":
                    _receipt_core(generation_receipt) == _receipt_core(late),
                "online_skill_stages": len(late.get("skill_stages", [])),
                "archived_skill_stages": len(generation_receipt.get("skill_stages", [])),
            }, indent=2))
        (evidence / "done").touch()
        assert process.wait(timeout=40) == 0, (evidence / "stdout.log").read_text()[-4000:]
        stdout_text = (evidence / "stdout.log").read_text()
        assert "*Error*" not in stdout_text, stdout_text[-4000:]
        assert "Traceback" not in stdout_text, stdout_text[-4000:]

        # 5. Offline receipts and archive maintenance stay inside the private tree.
        archived = {}
        for name, context_ref, receipt_id, session_id in (
            ("late", context, timeout_id, "accept-first"),
            ("blocked", context, blocked_id, "accept-second"),
        ):
            archived[name] = BridgeClient.archived_receipt(launch, context_ref, receipt_id,
                                                          session_id=session_id)
        assert _receipt_core(archived["late"]) == _receipt_core(late), archived["late"]
        assert _receipt_core(archived["blocked"]) == _receipt_core(blocked_receipt), (
            archived["blocked"])
        bridge_root = launch / ".cad/ai/agent/bridges"
        before_dry = _tree_digest([bridge_root])
        dry_run = maintain_router_logs(launch, apply=False, retention_days=0)
        after_dry = _tree_digest([bridge_root])
        assert dry_run["dry_run"] is True and not dry_run["errors"], dry_run
        assert before_dry == after_dry, "dry-run maintenance must not write"
        applied = maintain_router_logs(launch, apply=True, retention_days=0)
        assert not applied["errors"], applied
        assert applied["dry_run"] is False
        assert len(applied["generations"]) == 2, applied
        assert all(row["status"] == "archived" for row in applied["generations"]), applied
        assert _receipt_core(BridgeClient.archived_receipt(
            launch, context, timeout_id, session_id="accept-first")) == _receipt_core(late)
        assert _receipt_core(BridgeClient.archived_receipt(
            launch, context, blocked_id,
            session_id="accept-second")) == _receipt_core(blocked_receipt)
        receipt_cli = subprocess.run(
            [str(CAD_PYTHON), "-m", "sico", "router-receipt",
             "--launch-dir", str(launch), "--instance", fields["instance_id"],
             "--generation", fields["generation"], "--session", "accept-first",
             "--request", timeout_id],
            cwd=str(CAD / "ai/python"),
            env={**os.environ, "PYTHONPATH": os.pathsep.join(str(CAD / part) for part in ("sico/python", "ai/python", "common/python"))},
            capture_output=True, text=True, timeout=60,
        )
        assert receipt_cli.returncode == 0, receipt_cli.stderr
        assert _receipt_core(json.loads(receipt_cli.stdout)) == _receipt_core(late), (
            receipt_cli.stdout)
        maintenance_cli = subprocess.run(
            [str(CAD_PYTHON), "-m", "sico", "router-maintenance",
             "--launch-dir", str(launch)],
            cwd=str(CAD / "ai/python"),
            env={**os.environ, "PYTHONPATH": os.pathsep.join(str(CAD / part) for part in ("sico/python", "ai/python", "common/python"))},
            capture_output=True, text=True, timeout=60,
        )
        assert maintenance_cli.returncode in {0, 1}, maintenance_cli.stderr
        assert json.loads(maintenance_cli.stdout)["dry_run"] is True
        (evidence / "archive-receipts.json").write_text(json.dumps({
            "archived": archived, "dry_run": dry_run, "applied": applied,
            "cli_receipt_core_matches":
                _receipt_core(json.loads(receipt_cli.stdout)) == _receipt_core(late),
        }, indent=2))
    finally:
        if process.poll() is None:
            (evidence / "done").touch()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
