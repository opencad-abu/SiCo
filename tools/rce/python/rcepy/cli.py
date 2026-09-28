"""Command line interface for the Python RCE backend."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import sys
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rce")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("run", "generate", "prepare"):
        p = sub.add_parser(name)
        p.add_argument("config", help="RCE TOML config file")
        p.add_argument("--legacy-config", action="store_true", help="convert historical boolean encodings with diagnostics")
        p.add_argument("--lock-token", help=argparse.SUPPRESS)
        if name == "run":
            p.add_argument(
                "--dry-run",
                action="store_true",
                help="print stages without launching EDA tools",
            )
            p.add_argument(
                "--stop-after",
                choices=[
                    "cdl",
                    "gds",
                    "lvs",
                    "query",
                    "extract",
                    "xrc_lvs",
                    "xrc_pdb",
                    "xrc_fmt",
                ],
            )

    lock = sub.add_parser("lock", help="manage an RCE run-directory reservation")
    lock.add_argument("action", choices=("reserve", "check", "release"))
    lock.add_argument("run_dir", type=Path)
    lock.add_argument("token")

    index = sub.add_parser("dspf-index", help="build or reuse a DSPF index")
    index.add_argument("source", help="DSPF/SPF source file")
    index.add_argument("--force", action="store_true", help="rebuild the index")
    index.add_argument("--cache-dir", help="override the DSPF cache directory")
    index.add_argument("--json", action="store_true", help="emit JSON")

    info = sub.add_parser("dspf-info", help="show DSPF index information")
    info.add_argument("target", help="DSPF/SPF source or SQLite index")
    info.add_argument("--cache-dir", help="override the DSPF cache directory")
    info.add_argument("--json", action="store_true", help="emit JSON")

    gui = sub.add_parser("dspf-gui", help="open the DSPF analyzer")
    gui.add_argument("source", nargs="?", help="DSPF/SPF source or SQLite index")
    gui.add_argument("--cache-dir", help="override the DSPF cache directory")
    gui.add_argument("--force", action="store_true", help="rebuild on open")
    gui.add_argument(
        "--bridge",
        nargs="?",
        const="stdio",
        choices=("stdio",),
        help="enable the Virtuoso JSONL bridge",
    )
    return parser


def _jsonable(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return _jsonable(value.to_dict())
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _print_result(value: Any, *, as_json: bool) -> None:
    payload = _jsonable(value)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    if not isinstance(payload, dict):
        print(payload)
        return
    for key, item in payload.items():
        if isinstance(item, dict):
            rendered = ", ".join(f"{name}={count}" for name, count in item.items())
        else:
            rendered = item
        print(f"{key}: {rendered}")


def _run_dspf_index(args: argparse.Namespace) -> int:
    from .dspf.indexer import build_index

    result = build_index(
        args.source,
        cache_dir=args.cache_dir,
        force=args.force,
    )
    _print_result(result, as_json=args.json)
    return 0


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _run_dspf_info(args: argparse.Namespace) -> int:
    from .dspf.indexer import build_index
    from .dspf.repository import DspfRepository

    target = Path(args.target).expanduser()
    if _is_sqlite(target):
        index_path = target.resolve()
    else:
        index_path = build_index(target, cache_dir=args.cache_dir).index_path
    with DspfRepository(index_path) as repository:
        payload = _jsonable(repository.info())
    if isinstance(payload, dict):
        payload.setdefault("index_path", str(index_path))
    _print_result(payload, as_json=args.json)
    return 0


def _run_dspf_gui(args: argparse.Namespace) -> int:
    # Keep Qt optional for every other RCE command.
    from .dspf_gui.app import run_gui

    return run_gui(
        source=args.source,
        cache_dir=args.cache_dir,
        force=args.force,
        bridge=args.bridge == "stdio",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command
    try:
        if command == "lock":
            from . import run_lock

            getattr(run_lock, args.action)(args.run_dir, args.token)
            return 0
        if command == "dspf-index":
            return _run_dspf_index(args)
        if command == "dspf-info":
            return _run_dspf_info(args)
        if command == "dspf-gui":
            return _run_dspf_gui(args)
        # DSPF-only commands do not parse RCE TOML or load the flow runner.
        from .config import load_config
        from .runner import RceRunner

        cfg = load_config(args.config, legacy=args.legacy_config)
        runner = RceRunner(
            cfg,
            generate_only=(command == "generate"),
            dry_run=getattr(args, "dry_run", False),
            stop_after=getattr(args, "stop_after", None),
            lock_token=args.lock_token,
        )
        return runner.prepare() if command == "prepare" else runner.run()
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"[RCE][ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
