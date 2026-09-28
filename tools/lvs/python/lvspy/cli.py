"""Command line interface for the LVS-only backend."""

from __future__ import annotations

import argparse
import sys

from rcepy.config import load_config

from .runner import LvsRunner, open_rve
from .single_stage import SingleStageRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lvs")
    sub = parser.add_subparsers(dest="command")

    for name in ("run", "generate"):
        p = sub.add_parser(name)
        p.add_argument("config", help="LVS TOML config file")
        p.add_argument("--legacy-config", action="store_true", help="convert historical boolean encodings with diagnostics")
        p.add_argument("--dry-run", action="store_true", help="print stages without launching EDA tools")
        p.add_argument("--stop-after", choices=["cdl", "gds", "lvs"])

    report = sub.add_parser("report")
    report.add_argument("config", help="LVS TOML config file")
    report.add_argument("--legacy-config", action="store_true", help="convert historical boolean encodings with diagnostics")
    report.add_argument("--dry-run", action="store_true", help="print calibre RVE command only")

    for name in ("stream-gds", "export-cdl"):
        p = sub.add_parser(name)
        p.add_argument("config", help="stage TOML config file")
        p.add_argument("--legacy-config", action="store_true", help="convert historical boolean encodings with diagnostics")
        p.add_argument("--dry-run", action="store_true", help="print stage command without launching EDA tools")
        p.add_argument("--generate-only", action="store_true", help="generate command file only")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"
    try:
        cfg = load_config(args.config, legacy=args.legacy_config)
        if command == "report":
            return open_rve(cfg, dry_run=getattr(args, "dry_run", False))
        if command in {"stream-gds", "export-cdl"}:
            stage = "gds" if command == "stream-gds" else "cdl"
            runner = SingleStageRunner(
                cfg,
                stage,
                generate_only=getattr(args, "generate_only", False),
                dry_run=getattr(args, "dry_run", False),
            )
            return runner.run()
        runner = LvsRunner(
            cfg,
            generate_only=(command == "generate"),
            dry_run=getattr(args, "dry_run", False),
            stop_after=getattr(args, "stop_after", None),
        )
        return runner.run()
    except Exception as exc:
        print(f"[LVS][ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
