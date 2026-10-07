"""Command adapter for split run evidence verification."""

from __future__ import annotations

import argparse
from .cli_output import print_json


def verify_run(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .manifest import verify_split_manifests

    result = verify_split_manifests(
        Path(args.control_manifest),
        Path(args.payload_manifest),
        verify_artifacts=not args.no_artifacts,
    )
    if args.json:
        print_json(result)
    else:
        print(f"status: {result['status']}")
        print(f"run_id: {result['run_id']}")
        print(f"control_manifest: {result['control_manifest']}")
        print(f"payload_manifest: {result['payload_manifest']}")
        print(f"artifacts_verified: {result['artifacts_verified']}")
    return 0
