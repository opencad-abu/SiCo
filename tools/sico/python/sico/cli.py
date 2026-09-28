"""Runnable first increment: demo, durable history, or a Virtuoso-bound relay session."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

from cadai.codex_selection import CodexConfigurationError

from . import PRODUCT_NAME
from .adapters.virtuoso import context_tools
from .demo import DemoPeer
from .providers.config import load_provider
from .service.backend import create_backend
from .service.connection import connection_instructions
from .service.service_lifecycle import DEFAULT_IDLE_SECONDS
from .storage.history import SessionReader
from .storage.journal import SessionJournal, open_private
from .transport.broker import ContextBroker
from .transport.framing import ProtocolError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=PRODUCT_NAME + " (Python Agent)")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("gateway-worker", "demo", "connect", "history", "relay", "bridge", "background-relay",
                 "background-watch", "background-node", "router-receipt",
                 "router-maintenance", "launch",
                 "desktop-worker", "quick-input", "project-service", "service-status",
                 "service-stop", "host-submit", "host-input", "service-gui", "migration-audit"),
    )
    parser.add_argument("--launch-dir", default=os.getcwd())
    parser.add_argument("--project-dir", help="Explicit project directory for service commands")
    parser.add_argument("--bootstrap-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--idle-seconds", type=float, default=DEFAULT_IDLE_SECONDS)
    parser.add_argument("--session", default=None)
    parser.add_argument("--service-id", help=argparse.SUPPRESS)
    parser.add_argument("--runtime-id", help=argparse.SUPPRESS)
    parser.add_argument("--message", default=None)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--connection-file")
    parser.add_argument("--context-file", help=(
        "Protected .cxt for connect; otherwise use SICO_AI_CONTEXT or the installed context"))
    parser.add_argument("--instance")
    parser.add_argument("--generation")
    parser.add_argument("--target", default="bound")
    parser.add_argument("--request")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--abandon-interrupted", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="强制停止本机当前工程的 Agent Service，保留会话记录")
    parser.add_argument("--apply", action="store_true",
                        help="Apply router log maintenance; default is dry-run")
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--max-bytes", type=int, default=512 * 1024 * 1024)
    parser.add_argument(
        "--provider-config",
        help="Explicit provider JSON; otherwise use SICO_* or project config",
    )
    args = parser.parse_args(argv)
    if args.command == "gateway-worker":
        from .codex.gateway_worker import main as gateway_main

        return gateway_main()
    if args.force and args.command != "service-stop":
        parser.error("--force 仅用于 service-stop")
    if args.command is None:
        args.command, args.gui = "home", True
    if args.command == "home":
        from sico_ui.home_desktop import run_home

        if args.context_file:
            parser.error("--context-file is only supported by connect")
        return run_home(args)
    if args.context_file and args.command != "connect":
        parser.error("--context-file is only supported by connect")
    if args.command == "service-gui" and not args.session:
        parser.error("service-gui requires --session")
    if args.command == "migration-audit":
        from .migration.audit import audit

        if args.apply:
            parser.error("migration-audit is read-only; --apply is not supported")
        if not args.project_dir or not Path(args.project_dir).is_absolute():
            parser.error("migration-audit requires an absolute --project-dir")
        try:
            report = audit(args.project_dir)
        except (OSError, ValueError):
            parser.error("Project state is unavailable for migration audit")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["blockers"] else 0
    if args.command in {"project-service", "service-status", "service-stop"}:
        from .service.service_cli import run_admin
        from .service.service_entry import run_service

        if not args.project_dir or not Path(args.project_dir).is_absolute():
            parser.error(args.command + " requires an absolute --project-dir")
        if args.command != "project-service":
            return run_admin(args)
        return run_service(args.project_dir, bootstrap_fd=args.bootstrap_fd,
                           idle_seconds=args.idle_seconds)
    if args.command in {"host-submit", "host-input"}:
        from .service.host_process import run_host_submit

        try:
            return run_host_submit(args)
        except (ProtocolError, ValueError, OSError):
            print(json.dumps({"kind": "rejected", "message": "宿主输入未接收，文字已保留。"}),
                  flush=True)
            return 2
    if args.command == "router-maintenance":
        from .transport.router_archive import maintain_router_logs

        try:
            report = maintain_router_logs(args.launch_dir, apply=args.apply,
                retention_days=args.retention_days, max_bytes=args.max_bytes)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report['errors'] or report['over_budget'] else 0
    if args.command == "background-node":
        from .service.lsf_bundle import run_node

        if not args.connection_file:
            parser.error("background-node requires --connection-file")
        return run_node(args.connection_file)
    if args.command in {"background-relay", "background-watch"}:
        from .service.background_worker import background_relay, background_watch

        if not args.connection_file:
            parser.error(args.command + " requires --connection-file")
        operation = background_relay if args.command == "background-relay" else background_watch
        return operation(args.connection_file)
    if args.command == "router-receipt":
        from .core.contracts import BoundContext, NeedsReconcile
        from .transport.bridge_client import BridgeClient

        if not all((args.instance, args.generation, args.session, args.request)):
            parser.error(
                "router-receipt requires --instance, --generation, --session and --request")
        try:
            context = BoundContext(args.instance, args.generation, args.target, {})
            receipt = BridgeClient.archived_receipt(args.launch_dir, context, args.request,
                                                    session_id=args.session)
        except (OSError, ValueError, NeedsReconcile) as exc:
            parser.error(str(exc))
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    if args.command == "bridge":
        from .service.launch import launch_bridge

        return launch_bridge(args)
    if args.command == "quick-input":
        from .service.composer import run_composer

        return run_composer(args)
    if args.command in {"launch", "desktop-worker"}:
        from .service.desktop import run_desktop
        from .service.launch import launch_desktop

        return (launch_desktop if args.command == "launch" else run_desktop)(args)
    if args.command == "relay":
        from .transport.relay import relay

        if not args.connection_file:
            parser.error("relay requires --connection-file")
        fd = open_private(Path(args.connection_file), os.O_RDONLY)
        with os.fdopen(fd, "r") as stream:
            config = json.load(stream)
        relay(config["host"], config["port"], config["token"])
        return 0
    if args.command == "history":
        if not args.session:
            parser.error("history requires --session")
        launch = Path(args.launch_dir).absolute()
        state = launch / ".sico"
        try:
            state.lstat()
        except FileNotFoundError:
            state = launch / ".cad"
        reader = SessionReader(state / "ai" / "agent", args.session)
        for event in reader.events():
            print(json.dumps(event, ensure_ascii=False))
        return 0
    if args.gui or args.command == "service-gui":
        from .service.local_startup import run_local_gui

        return run_local_gui(args)
    session_id = args.session or uuid.uuid4().hex
    try:
        provider = load_provider(args.provider_config, launch_dir=args.launch_dir)
    except CodexConfigurationError as exc:
        parser.error(str(exc))
    except (ValueError, OSError) as exc:
        parser.error(
            f"Provider configuration is invalid ({type(exc).__name__}); check SICO_* or JSON"
        )
    with (
        SessionJournal(args.launch_dir, session_id) as journal,
        ContextBroker(min(args.timeout, 30)) as broker,
    ):
        peer = None
        try:
            if args.command == "demo":
                peer = DemoPeer(broker, journal.state.context if journal.state else None)
                context = peer.context
            else:
                if journal.state:
                    parser.error(
                        "Rebinding a real session is not supported yet; use a new session ID"
                    )
                instance, generation = uuid.uuid4().hex, uuid.uuid4().hex
                broker.expected_identity = instance, generation
                try:
                    instructions = connection_instructions(
                        journal, broker, instance, generation, context_file=args.context_file,
                    )
                except ValueError as exc:
                    parser.error(str(exc))
                print(instructions, flush=True)
                context = broker.context(instance, timeout=args.timeout)
            print(f"session={session_id}  provider={provider.label}", flush=True)
            broker.register_session(session_id, context)
            loop = create_backend(provider, context_tools(broker, journal), journal, context)
            if args.abandon_interrupted:
                loop.acknowledge_interrupted()
            for event in loop.run(args.message or "请读取并说明当前绑定的设计上下文。"):
                print(json.dumps(event, ensure_ascii=False), flush=True)
            return 0 if loop.state.task["status"] == "completed" else 1
        finally:
            if "loop" in locals() and hasattr(loop, "close"):
                loop.close()
            if peer:
                peer.close()


if __name__ == "__main__":
    raise SystemExit(main())
