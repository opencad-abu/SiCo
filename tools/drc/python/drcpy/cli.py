"""Command line interface for the DRC-only backend."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from rcepy.config import load_config

from .rule_select import discover_rule_groups
from .runner import DrcRunner, open_rve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="drc")
    sub = parser.add_subparsers(dest="command")
    for name in ("run", "generate"):
        p = sub.add_parser(name)
        p.add_argument("config", help="DRC TOML config file")
        p.add_argument("--legacy-config", action="store_true", help="convert historical boolean encodings with diagnostics")
        p.add_argument(
            "--dry-run",
            action="store_true",
            help="print stages without launching EDA tools",
        )
        p.add_argument("--stop-after", choices=["gds", "drc"])
    report = sub.add_parser("report")
    report.add_argument("config", help="DRC TOML config file")
    report.add_argument("--legacy-config", action="store_true", help="convert historical boolean encodings with diagnostics")
    report.add_argument(
        "--dry-run", action="store_true", help="print calibre RVE command only"
    )
    groups = sub.add_parser("rule-groups", help="discover DRC rule groups")
    groups.add_argument("rule_file", help="Calibre SVRF or compile-time TVF rule file")
    groups.add_argument(
        "--static", action="store_true", help="skip Calibre TVF expansion"
    )
    groups.add_argument("--calibre", help="Calibre executable used for TVF expansion")
    groups.add_argument("--cache-dir", help="cache directory for expanded TVF files")
    groups.add_argument(
        "--timeout", type=float, default=60.0, help="TVF expansion timeout in seconds"
    )
    groups.add_argument("--output", help="write output to a file")
    groups_formats = groups.add_mutually_exclusive_group()
    groups_formats.add_argument(
        "--json", action="store_true", help="emit structured JSON"
    )
    groups_formats.add_argument(
        "--tsv", action="store_true", help="emit group/count TSV"
    )
    groups_formats.add_argument(
        "--skill-tsv",
        action="store_true",
        help="emit metadata plus group/count TSV for the Virtuoso UI",
    )
    selector = sub.add_parser(
        "rule-select-gui", help="open the PyQt5 DRC rule selector"
    )
    selector.add_argument(
        "rule_file", help="Calibre SVRF or compile-time TVF rule file"
    )
    selector.add_argument(
        "--initial", help="GROUP/CHECK TSV containing the current selection"
    )
    selector.add_argument(
        "--output", required=True, help="write the applied GROUP/CHECK TSV here"
    )
    selector.add_argument("--calibre", help="Calibre executable used for TVF expansion")
    selector.add_argument("--cache-dir", help="cache directory for expanded TVF files")
    selector.add_argument(
        "--timeout", type=float, default=60.0, help="TVF expansion timeout in seconds"
    )
    selector.add_argument(
        "--static", action="store_true", help="skip Calibre TVF expansion"
    )
    selector.add_argument(
        "--parent-pid", type=int, help="owning Virtuoso PID to monitor"
    )
    return parser


def _write_output(path: Path, text: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"
    try:
        if command == "rule-select-gui":
            # Keep PyQt5 out of batch DRC processes and command-line-only tests.
            from .rule_select_gui import run_gui

            return run_gui(
                args.rule_file,
                initial_path=args.initial,
                output_path=args.output,
                calibre=args.calibre,
                cache_dir=args.cache_dir,
                timeout=args.timeout,
                expand_tvf=not args.static,
                parent_pid=args.parent_pid,
            )
        if command == "rule-groups":
            groups = discover_rule_groups(
                args.rule_file,
                calibre=args.calibre,
                timeout=args.timeout,
                cache_dir=args.cache_dir,
                expand_tvf=not args.static,
            )
            if args.json:
                rendered = json.dumps(groups.to_dict(), ensure_ascii=True) + "\n"
            elif args.skill_tsv:
                safe_error = (groups.error or "").translate(
                    str.maketrans({"\\": "\\\\", "\t": "\\t", "\r": "\\r", "\n": "\\n"})
                )
                rendered = "".join(
                    (
                        f"#source\t{groups.source}\n",
                        f"#cached\t{'true' if groups.cached else 'false'}\n",
                        f"#error\t{safe_error}\n",
                        *(
                            f"{name}\t{groups.counts.get(name, 0)}\t"
                            + "\t".join((groups.members or {}).get(name, ()))
                            + "\n"
                            for name in groups.groups
                        ),
                    )
                )
            elif args.tsv:
                rendered = "".join(
                    f"{name}\t{groups.counts.get(name, 0)}\n" for name in groups.groups
                )
            else:
                rendered = "\n".join(groups.groups)
                if rendered:
                    rendered += "\n"
            if args.output:
                output = Path(args.output).expanduser()
                _write_output(output, rendered)
            else:
                sys.stdout.write(rendered)
            return 0
        cfg = load_config(args.config, legacy=args.legacy_config)
        if command == "report":
            return open_rve(cfg, dry_run=getattr(args, "dry_run", False))
        runner = DrcRunner(
            cfg,
            generate_only=(command == "generate"),
            dry_run=getattr(args, "dry_run", False),
            stop_after=getattr(args, "stop_after", None),
        )
        return runner.run()
    except Exception as exc:
        print(f"[DRC][ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
