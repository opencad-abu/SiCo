"""Command-line interface for shared CAD configuration profiles."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .model import SUPPORTED_FLOWS, ProfileDocument, ProfileValue, load_profile
from .skill_data import publish_profile, write_form_data


def _json_value(value: ProfileValue) -> Any:
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _json_document(document: ProfileDocument) -> str:
    payload = {
        "flow": document.flow,
        "version": document.version,
        "source": str(document.source),
        "values": {
            path: _json_value(value) for path, value in document.entries
        },
    }
    return json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sico-profile")
    subparsers = parser.add_subparsers(dest="command")

    validate = subparsers.add_parser("validate", help="validate a profile TOML")
    validate.add_argument("--flow", required=True, choices=sorted(SUPPORTED_FLOWS))
    validate.add_argument("config")
    validate.add_argument(
        "--json", action="store_true", help="print normalized values as JSON"
    )

    form_data = subparsers.add_parser(
        "form-data", help="write validated inert SKILL form data"
    )
    form_data.add_argument("--flow", required=True, choices=sorted(SUPPORTED_FLOWS))
    form_data.add_argument("config")
    form_data.add_argument("output")

    publish = subparsers.add_parser(
        "publish", help="atomically normalize and publish a profile TOML"
    )
    publish.add_argument("--flow", required=True, choices=sorted(SUPPORTED_FLOWS))
    publish.add_argument("source")
    publish.add_argument("output")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help(sys.stderr)
        return 2
    try:
        config = args.source if args.command == "publish" else args.config
        document = load_profile(config, args.flow)
        if args.command == "form-data":
            write_form_data(document, args.output)
        elif args.command == "publish":
            publish_profile(document, args.output)
        elif args.json:
            sys.stdout.write(_json_document(document))
        return 0
    except Exception as exc:
        print(f"[CAD-PROFILE][ERROR] {exc}", file=sys.stderr)
        return 1
