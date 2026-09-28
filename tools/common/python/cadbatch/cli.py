"""Command line interface for the shared CAD batch controller."""

from __future__ import annotations

import argparse
import sys

from .controller import BatchController, finalize_publications, load_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sico-batch")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run every task in a batch manifest")
    run.add_argument("manifest", help="batch TOML manifest")
    finalize = sub.add_parser(
        "finalize", help="merge serialized CIW publication results"
    )
    finalize.add_argument("manifest", help="batch TOML manifest")
    finalize.add_argument("publications", help="publication result TSV")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        if args.command == "finalize":
            return finalize_publications(manifest, args.publications)
        return BatchController(manifest).run()
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"[BATCH][ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
