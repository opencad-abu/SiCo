"""Command-line interface for shared LSF discovery."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sys
from threading import Event
from typing import Sequence

from cadgui.protocol import PROTOCOL_VERSION, write_transfer
from cadgui.lifecycle import capture_parent_identity
from cadgui.transfer import atomic_publish_text

from .cache import SessionTopologyCache, default_cache_root
from .collector import (
    CollectorCancelled,
    CollectorConfig,
    LsfCollector,
    SubprocessRunner,
    current_os_user,
)
from .model import ClusterSnapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sico-lsf")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--bqueues", default="bqueues")
    parser.add_argument("--bhosts", default="bhosts")
    parser.add_argument("--lsload", default="lsload")
    parser.add_argument("--lshosts", default="lshosts")
    parser.add_argument("--bjobs", default="bjobs")
    # Resolve after parsing, so --help and an explicit --bkill remain usable
    # when a site is migrating its environment.
    parser.add_argument("--bkill")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("queues", "hosts", "snapshot"):
        command = subparsers.add_parser(name)
        command.add_argument("--format", choices=("json", "tsv"), default="json")
        command.add_argument("--output", type=Path)
        command.add_argument("--session-pid", type=int)
        if name in {"hosts", "snapshot"}:
            command.add_argument("--queue", required=name == "hosts")
    monitor = subparsers.add_parser(
        "monitor", help="open the PyQt5 LSF Load Monitor"
    )
    monitor.add_argument("--queue", help="initial queue preference")
    monitor.add_argument(
        "--refresh-interval",
        type=int,
        default=10,
        help="automatic refresh interval in seconds (default: 10)",
    )
    monitor.add_argument(
        "--parent-pid", type=int, help="owning Virtuoso PID to monitor"
    )
    monitor.add_argument(
        "--output",
        type=Path,
        help="publish an applied Queue/Host TSV selection and enable selector mode",
    )
    return parser


def _transfer_records(snapshot: ClusterSnapshot, command: str) -> tuple[tuple[str, str], ...]:
    records: list[tuple[str, str]] = []
    if command in {"queues", "snapshot"}:
        records.extend(("QUEUE", queue.name) for queue in snapshot.queues)
    if command in {"hosts", "snapshot"}:
        if snapshot.selected_queue:
            records.append(("SELECTED_QUEUE", snapshot.selected_queue))
        records.extend(("HOST", host.name) for host in snapshot.hosts)
    return tuple(records)


def _write_result(
    snapshot: ClusterSnapshot,
    command: str,
    output_format: str,
    output: Path | None,
) -> None:
    if output_format == "json":
        text = json.dumps(snapshot.to_dict(), ensure_ascii=True, sort_keys=True) + "\n"
        if output is None:
            sys.stdout.write(text)
        else:
            atomic_publish_text(output, text)
        return
    records = _transfer_records(snapshot, command)
    if output is not None:
        write_transfer(
            output,
            records,
            status=snapshot.status,
            version=PROTOCOL_VERSION,
        )
        return
    sys.stdout.write(f"#version\t{PROTOCOL_VERSION}\n")
    sys.stdout.write(f"#status\t{snapshot.status}\n")
    for kind, value in records:
        sys.stdout.write(f"{kind}\t{value}\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.bkill is None:
        if "SICO_LSF_BKILL" not in os.environ and "EXT_LSF_BKILL" in os.environ:
            print("sico-lsf: EXT_LSF_BKILL was renamed; set SICO_LSF_BKILL",
                  file=sys.stderr)
            return 2
        args.bkill = os.environ.get("SICO_LSF_BKILL") or "bkill"
    config = CollectorConfig(
        bqueues=args.bqueues,
        bhosts=args.bhosts,
        lsload=args.lsload,
        bjobs=args.bjobs,
        bkill=args.bkill,
        timeout=args.timeout,
        lshosts=args.lshosts,
    )
    if args.command == "monitor":
        if not 2 <= args.refresh_interval <= 3600:
            print(
                "sico-lsf: --refresh-interval must be between 2 and 3600 seconds",
                file=sys.stderr,
            )
            return 2
        if args.output is not None:
            output = args.output.expanduser()
            if output.exists():
                print(
                    f"sico-lsf: selector output already exists: {output}",
                    file=sys.stderr,
                )
                return 2
            if not output.parent.is_dir():
                print(
                    f"sico-lsf: selector output directory does not exist: {output.parent}",
                    file=sys.stderr,
                )
                return 2
        try:
            # Keep Qt out of queues/hosts/snapshot processes.
            from .gui.app import run_gui

            return run_gui(
                config=config,
                initial_queue=args.queue,
                refresh_interval=args.refresh_interval,
                parent_pid=args.parent_pid,
                output=args.output,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"sico-lsf: {exc}", file=sys.stderr)
            return 2
    topology_cache = None
    if args.session_pid is not None:
        try:
            session_identity = capture_parent_identity(args.session_pid)
        except RuntimeError as exc:
            print(f"sico-lsf: {exc}", file=sys.stderr)
            return 2
        if session_identity is not None:
            try:
                cache_root = default_cache_root(create=True)
            except (OSError, ValueError) as exc:
                print(f"sico-lsf: {exc}", file=sys.stderr)
                return 2
            topology_cache = SessionTopologyCache(
                cache_root,
                session_pid=session_identity[0],
                session_start_time=session_identity[1],
                user=current_os_user(),
                command_signature=(config.bqueues, config.bhosts, config.lsload),
            )
    cancel_event = Event()
    received_signal: list[int] = []

    def request_cancel(signum: int, _frame: object) -> None:
        received_signal.append(signum)
        cancel_event.set()

    previous_handlers = {
        signum: signal.signal(signum, request_cancel)
        for signum in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        collector = LsfCollector(
            config=config,
            topology_cache=topology_cache,
            runner=SubprocessRunner(cancelled=cancel_event.is_set),
        )
        if args.command == "queues":
            snapshot = collector.snapshot(include_hosts=False)
        else:
            snapshot = collector.snapshot(
                args.queue, include_jobs=args.command == "snapshot"
            )
        if cancel_event.is_set():
            raise CollectorCancelled("LSF collection was cancelled")
    except CollectorCancelled:
        return 128 + (received_signal[-1] if received_signal else signal.SIGTERM)
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    _write_result(snapshot, args.command, args.format, args.output)
    for diagnostic in snapshot.diagnostics:
        print(f"[CAD-LSF][{diagnostic.severity.upper()}] {diagnostic.message}", file=sys.stderr)
    return 2 if snapshot.status == "error" else 0
