"""Command line interface for the LEF generation flow."""

from __future__ import annotations

import argparse
import sys

from .config import load_config
from .form_data import write_form_data
from .runner import LefRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lef")
    sub = parser.add_subparsers(dest="command")
    for name in ("run", "generate"):
        command = sub.add_parser(name)
        command.add_argument("config", help="LEF TOML config file")
        command.add_argument(
            "--dry-run",
            action="store_true",
            help="generate inputs and print the Abstract Generator command",
        )
    form_data = sub.add_parser("form-data")
    form_data.add_argument("config", help="LEF TOML config file")
    form_data.add_argument("output", help="exclusive output path for SKILL form data")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help(sys.stderr)
        return 2
    try:
        cfg = load_config(args.config)
        if args.command == "form-data":
            write_form_data(cfg, args.output)
            return 0
        runner = LefRunner(
            cfg,
            generate_only=args.command == "generate",
            dry_run=args.dry_run,
        )
        return runner.run()
    except Exception as exc:
        print(f"[LEF][ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
