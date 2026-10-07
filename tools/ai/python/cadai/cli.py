"""Command-line entry points used by Virtuoso and supported AI agents."""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path
from sicoenv import read

from .controller import DEFAULT_REQUEST_TIMEOUT, run_session
from .agent_profile import INTERACTIVE_PROFILE, SUPPORTED_PROFILES
from .launch import production_python
from .lsf import exec_lsf_session


def _timeout_from_environment(current: str, default: float) -> float:
    return float(read(os.environ, current, default))


def run_mcp_from_environment(timeout: float) -> int:
    from .mcp import run_mcp_from_environment as run

    return run(timeout)


def _add_session_arguments(session: argparse.ArgumentParser) -> None:
    session.add_argument("--workspace", type=Path, required=True)
    session.add_argument("--agent", choices=("codex", "claude"), default="codex")
    session.add_argument("--profile", choices=sorted(SUPPORTED_PROFILES), default=INTERACTIVE_PROFILE)
    session.add_argument("--terminal")
    session.add_argument(
        "--codex",
        help="Codex executable override (default: bundled native runtime)",
    )
    session.add_argument(
        "--claude",
        help="fallback Claude executable when $SICO_HOME/tools/ai/bin/claude is unavailable",
    )
    session.add_argument(
        "--request-timeout",
        type=float,
        default=_timeout_from_environment(
            "SICO_AI_REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT
        ),
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="sico-ai")
    root.add_argument("--version", action="version", version="sico-ai 0.1.0")
    commands = root.add_subparsers(dest="command", required=True)

    session = commands.add_parser("session", help="launch one managed AI Assistant terminal")
    _add_session_arguments(session)
    session.add_argument("--shared-spool", action="store_true", help=argparse.SUPPRESS)
    lsf_session = commands.add_parser(
        "lsf-session",
        help="handoff one managed AI Assistant session to interactive LSF",
    )
    _add_session_arguments(lsf_session)

    mcp = commands.add_parser("mcp", help="serve MCP over stdio for the current session")
    mcp.add_argument(
        "--request-timeout",
        type=float,
        default=_timeout_from_environment(
            "SICO_AI_MCP_TIMEOUT", DEFAULT_REQUEST_TIMEOUT + 5
        ),
    )
    return root


def _remote_session_command(args: argparse.Namespace) -> list[str]:
    workspace = args.workspace.expanduser().absolute()
    if not workspace.is_dir():
        raise NotADirectoryError(workspace)
    entry = Path(sys.argv[0]).expanduser()
    if not entry.is_absolute():
        entry = Path.cwd() / entry
    if not entry.is_file():
        raise FileNotFoundError(f"current sico-ai entry is unavailable: {entry}")
    entry = entry.absolute()
    with entry.open("rb") as stream:
        is_native = stream.read(4) == b"\x7fELF"
    if is_native:
        invocation = [str(entry)]
    elif entry.name == "__main__.py" and entry.parent.name == "cadai":
        script_entry = entry.parent.parent / "sico-ai"
        invocation = (
            [production_python(require_environment=False), str(script_entry)]
            if script_entry.is_file()
            else [production_python(require_environment=False), "-m", "cadai"]
        )
    else:
        invocation = [production_python(require_environment=False), str(entry)]
    command = [
        *invocation,
        "session",
        "--shared-spool",
        "--workspace",
        str(workspace),
        "--agent",
        args.agent,
        "--request-timeout",
        f"{args.request_timeout:g}",
    ]
    if args.profile != INTERACTIVE_PROFILE:
        command.extend(["--profile", args.profile])
    for option in ("terminal", "codex", "claude"):
        if value := getattr(args, option):
            command.extend([f"--{option}", value])
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command in {"session", "lsf-session"}:
            if not math.isfinite(args.request_timeout) or args.request_timeout <= 0:
                raise ValueError("request timeout must be a positive finite number")
            if args.command == "lsf-session":
                exec_lsf_session(_remote_session_command(args))
                return 0
            session_kwargs = {
                "agent": args.agent,
                "claude": args.claude,
                "shared_spool": args.shared_spool,
            }
            if args.profile != INTERACTIVE_PROFILE:
                session_kwargs["profile"] = args.profile
            return run_session(
                args.workspace,
                args.terminal,
                args.codex,
                args.request_timeout,
                **session_kwargs,
            )
        if not math.isfinite(args.request_timeout) or args.request_timeout <= 0:
            raise ValueError("request timeout must be a positive finite number")
        return run_mcp_from_environment(args.request_timeout)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"sico-ai: {exc}", file=sys.stderr)
        return 2


__all__ = ["main", "parser"]
