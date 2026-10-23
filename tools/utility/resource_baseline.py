"""Measure reproducible SiCo journal and transport resource baselines.

This is a source-test measurement, not a production NAS/EDA qualification. It
uses a private temporary project, synthetic journal records and a local pipe.
The default full run is intentionally bounded; ``--quick`` is for diagnostics.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import resource
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BUDGETS = {
    "first_screen_p95_ms": 500.0,
    "display_projection_p95_ms": 750.0,
    "append_p95_ms": 25.0,
    "slow_disk_append_p95_ms": 100.0,
    "slow_disk_lock_wait_p95_ms": 100.0,
    # A 40-append burst with a deliberately 5 ms fsync can hold the journal
    # owner for ~200 ms; the bound covers one complete bounded burst.
    "slow_disk_burst_lock_wait_p95_ms": 300.0,
    "multi_session_append_p95_ms": 50.0,
    "cancel_p95_ms": 150.0,
    "rss_delta_mb": 512.0,
}


def _paths():
    spec = json.loads(
        (ROOT / "tools/utility/a_philosophy_compatibility.json").read_text())
    return [ROOT / path for path in spec["search_paths"]]


def _imports():
    for path in reversed(_paths()):
        sys.path.insert(0, str(path))
    from sico.codex.rpc_queue import (
        BoundedMessageQueue,
        MAX_RPC_PENDING_BYTES,
        MAX_RPC_QUEUE_BYTES,
    )
    from sico.codex.rpc_write import PipeWriter, WriteFailure
    from sico.core.contracts import BoundContext, RunState
    from sico.service.event_display import EventDisplay, coalesce
    from sico.storage.journal import SessionJournal
    return (BoundedMessageQueue, MAX_RPC_QUEUE_BYTES, MAX_RPC_PENDING_BYTES,
            PipeWriter, WriteFailure, BoundContext, RunState, SessionJournal,
            EventDisplay, coalesce)


def _percentiles(values):
    values = sorted(float(value) for value in values)
    if not values:
        return {"p50_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None}

    def pick(fraction):
        return values[max(0, min(len(values) - 1,
                                 math.ceil(len(values) * fraction) - 1))]

    return {"p50_ms": round(statistics.median(values), 3),
            "p95_ms": round(pick(.95), 3), "p99_ms": round(pick(.99), 3),
            "max_ms": round(values[-1], 3)}


def _peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _current_rss_mb():
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return float(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return _peak_rss_mb()


def _new_state(RunState, BoundContext):
    state = RunState(BoundContext("bench-instance", "bench-generation",
                                  "bench-target", {"valid": True}))
    state.task = {"id": "bench-task", "status": "executing", "text": "",
                  "pending_calls": []}
    return state


def _seed(SessionJournal, RunState, BoundContext, project, count):
    original = os.fsync
    os.fsync = lambda _fd: None
    try:
        with SessionJournal(project, "large-history") as journal:
            state = _new_state(RunState, BoundContext)
            started = time.perf_counter()
            for index in range(count):
                journal.append("model.delta", {"text": "x"}, state)
            elapsed = time.perf_counter() - started
            size = (journal.directory / "events.jsonl").stat().st_size
        return {"rows_seeded": count, "seed_seconds": round(elapsed, 3),
                "journal_bytes": size}
    finally:
        os.fsync = original


def _measure_large_history(SessionJournal, RunState, BoundContext, project,
                           rows, runs, EventDisplay, coalesce):
    seeded = _seed(SessionJournal, RunState, BoundContext, project, rows)
    before = _current_rss_mb()
    started = time.perf_counter()
    with SessionJournal(project, "large-history") as journal:
        opened = time.perf_counter() - started
        readings = []
        windows = []
        projection_readings = []
        for _ in range(runs):
            started = time.perf_counter()
            start, committed, _origins = journal.prepare_replay(200)
            readings.append((time.perf_counter() - started) * 1000.0)
            windows.append(committed - start + 1)
            events = journal.events(start - 1, limit=committed - start + 1)
            display = EventDisplay()
            started = time.perf_counter()
            for event in coalesce(events):
                display.project(event)
            projection_readings.append((time.perf_counter() - started) * 1000.0)
    after = _current_rss_mb()
    return {**seeded, "open_seconds": round(opened, 3),
            "first_screen": _percentiles(readings),
            "display_projection": _percentiles(projection_readings),
            "window_rows": max(windows), "runs": runs,
            "rss_before_mb": round(before, 3), "rss_after_mb": round(after, 3),
            "rss_delta_mb": round(after - before, 3),
            "rss_peak_mb": round(_peak_rss_mb(), 3)}


def _append(SessionJournal, RunState, BoundContext, project, count, delay=0.0):
    original = os.fsync
    if delay:
        def slow(fd):
            time.sleep(delay)
            return original(fd)
        os.fsync = slow
    try:
        with SessionJournal(project, "append") as journal:
            state = _new_state(RunState, BoundContext)
            readings = []
            for _ in range(count):
                started = time.perf_counter()
                journal.append("model.delta", {"text": "x" * 32}, state)
                readings.append((time.perf_counter() - started) * 1000.0)
            size = (journal.directory / "events.jsonl").stat().st_size
        return {"rows": count, "fsync_delay_ms": round(delay * 1000, 3),
                "journal_bytes": size, **_percentiles(readings)}
    finally:
        os.fsync = original


def _slow_lock_wait(SessionJournal, RunState, BoundContext, project,
                    name, appends, reads, delay, pause):
    original = os.fsync

    def slow(fd):
        time.sleep(delay)
        return original(fd)

    with SessionJournal(project, name) as journal:
        state = _new_state(RunState, BoundContext)
        for _ in range(32):
            journal.append("model.delta", {"text": "x"}, state)
        stop, errors = threading.Event(), []

        def writer():
            try:
                state = _new_state(RunState, BoundContext)
                for _ in range(appends):
                    journal.append("model.delta", {"text": "x" * 32}, state)
                    if pause:
                        time.sleep(pause)
            except BaseException as exc:
                errors.append(repr(exc))
            finally:
                stop.set()

        os.fsync = slow
        worker = threading.Thread(target=writer)
        try:
            worker.start()
            waits = []
            while not stop.is_set():
                started = time.perf_counter()
                journal.prepare_replay(200)
                waits.append((time.perf_counter() - started) * 1000.0)
                if len(waits) >= reads:
                    break
            worker.join(5)
            if worker.is_alive():
                errors.append("slow-disk writer did not stop")
            return {"reads": len(waits), "appends": appends,
                    "fsync_delay_ms": round(delay * 1000, 3),
                    "pause_ms": round(pause * 1000, 3),
                    "lock_wait": _percentiles(waits), "errors": errors}
        finally:
            os.fsync = original


def _queue_probe(BoundedMessageQueue, rpc_queue_bytes, rpc_pending_bytes):
    messages = BoundedMessageQueue(maxsize=8, max_bytes=4096)
    held = {"value": "x" * 1024}
    messages.put_sized(held, 4096)
    blocked = False
    try:
        messages.put_sized({"value": "y"}, 1, block=False)
    except Exception:
        blocked = True
    messages.get_nowait()
    return {"probe_max_items": 8, "probe_max_bytes": 4096,
            "rpc_max_items": 4096, "rpc_max_bytes": rpc_queue_bytes,
            "rpc_pending_bytes": rpc_pending_bytes,
            "blocked_at_limit": blocked,
            "queued_bytes_after_get": messages.queued_bytes}


def _cancel_probe(PipeWriter, WriteFailure, trials):
    readings = []
    errors = []
    for _ in range(trials):
        read_fd, write_fd = os.pipe()
        stream = os.fdopen(write_fd, "wb", buffering=0)
        stopped, cancelled = threading.Event(), threading.Event()
        writer = PipeWriter(stream, stopped)
        payload = b"x" * (8 * 1024 * 1024)

        def run():
            try:
                writer.write(payload, time.monotonic() + 10, cancelled.is_set)
            except WriteFailure:
                pass
            except BaseException as exc:
                errors.append(repr(exc))

        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(.05)
        started = time.perf_counter()
        cancelled.set()
        worker.join(2)
        if worker.is_alive():
            errors.append("cancelled writer did not stop")
        readings.append((time.perf_counter() - started) * 1000.0)
        try:
            writer.close()
        finally:
            os.close(read_fd)
    return {"trials": trials, "cancel": _percentiles(readings), "errors": errors}


def _multi_session(SessionJournal, RunState, BoundContext, project, sessions, rows):
    errors = []

    def one(index):
        try:
            with SessionJournal(project, "multi-" + str(index)) as journal:
                state = _new_state(RunState, BoundContext)
                values = []
                for _ in range(rows):
                    started = time.perf_counter()
                    journal.append("model.delta", {"text": "x" * 32}, state)
                    values.append((time.perf_counter() - started) * 1000.0)
                return values
        except BaseException as exc:
            errors.append(repr(exc))
            return []

    before = _current_rss_mb()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=sessions) as pool:
        values = list(pool.map(one, range(sessions)))
    elapsed = (time.perf_counter() - started) * 1000.0
    after = _current_rss_mb()
    readings = [value for group in values for value in group]
    return {"sessions": sessions, "rows_per_session": rows,
            "elapsed_ms": round(elapsed, 3), "append": _percentiles(readings),
            "rss_delta_mb": round(after - before, 3),
            "rss_peak_mb": round(_peak_rss_mb(), 3), "errors": errors}


def measure(quick=False):
    (BoundedMessageQueue, MAX_QUEUE_BYTES, MAX_PENDING_BYTES, PipeWriter,
     WriteFailure, BoundContext, RunState, SessionJournal, EventDisplay,
     coalesce) = _imports()
    rows = 5000 if quick else 50000
    small = 100 if quick else 500
    runs = 5 if quick else 20
    appends = 10 if quick else 40
    reads = 10 if quick else 40
    trials = 3 if quick else 10
    sessions, multi_rows = (2, 50) if quick else (4, 200)
    with tempfile.TemporaryDirectory(prefix="sico-eq11-") as directory:
        project = Path(directory)
        project.chmod(0o700)
        metrics = {
            "scope": "source-only synthetic local project; no Service/Virtuoso, EDA, model, LSF or SSH",
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "filesystem_probe": {"path": str(project), "statvfs": list(os.statvfs(project)[:4])},
            "large_history": _measure_large_history(
                SessionJournal, RunState, BoundContext, project, rows, runs,
                EventDisplay, coalesce),
            "append": _append(
                SessionJournal, RunState, BoundContext, project, small),
            "slow_disk": _append(
                SessionJournal, RunState, BoundContext, project, appends,
                delay=.005),
            "slow_disk_lock_wait": _slow_lock_wait(
                SessionJournal, RunState, BoundContext, project, "slow-lock",
                appends, reads, .005, .001),
            "slow_disk_burst_lock_wait": _slow_lock_wait(
                SessionJournal, RunState, BoundContext, project, "slow-burst",
                appends, 1, .005, 0),
            "queue": _queue_probe(
                BoundedMessageQueue, MAX_QUEUE_BYTES, MAX_PENDING_BYTES),
            "cancel": _cancel_probe(PipeWriter, WriteFailure, trials),
            "multi_session": _multi_session(
                SessionJournal, RunState, BoundContext, project, sessions, multi_rows),
        }
        checks = {
            "first_screen_p95_ms": metrics["large_history"]["first_screen"]["p95_ms"],
            "display_projection_p95_ms": metrics["large_history"][
                "display_projection"]["p95_ms"],
            "append_p95_ms": metrics["append"]["p95_ms"],
            "slow_disk_append_p95_ms": metrics["slow_disk"]["p95_ms"],
            "slow_disk_lock_wait_p95_ms": metrics["slow_disk_lock_wait"]["lock_wait"]["p95_ms"],
            "slow_disk_burst_lock_wait_p95_ms": metrics[
                "slow_disk_burst_lock_wait"]["lock_wait"]["p95_ms"],
            "multi_session_append_p95_ms": metrics["multi_session"]["append"]["p95_ms"],
            "cancel_p95_ms": metrics["cancel"]["cancel"]["p95_ms"],
            "rss_delta_mb": metrics["large_history"]["rss_delta_mb"],
        }
        violations = {name: value for name, value in checks.items()
                      if value is None or value > BUDGETS[name]}
        return {"status": "passed" if not violations else "failed",
                "passed": not violations, "budgets": BUDGETS,
                "checks": checks, "violations": violations, "metrics": metrics}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--output", type=Path,
                        default=ROOT / ".cad/resource-baseline/summary.json")
    args = parser.parse_args(argv)
    result = measure(args.quick)
    args.output = args.output if args.output.is_absolute() else ROOT / args.output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "violations": result["violations"],
                      "checks": result["checks"], "output": str(args.output)},
                     ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
