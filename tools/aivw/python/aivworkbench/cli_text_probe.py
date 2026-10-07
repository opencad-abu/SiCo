"""Command adapter for the disposable text-view qualification probe."""

from __future__ import annotations

import argparse
from .errors import AivwError
from .profiles import load_profile
from .workspace import resolve_launch_paths
from .cli_output import print_json


def probe_text_view(args: argparse.Namespace) -> int:
    import shutil

    from .text_view_probe import run_systemverilog_text_view_probe
    from .toolchain import capture_module_environment, probe_tools

    profile = load_profile(args.profile)
    environment = capture_module_environment(profile.modules)
    qualified = probe_tools(profile.tools, environment)
    dbaccess = next(
        (
            item.path
            for item in qualified
            if item.name == "dbaccess" and item.status == "PASS"
        ),
        "",
    )
    cds_text_to_5x = shutil.which("cdsTextTo5x", path=environment.get("PATH")) or ""
    if not dbaccess or not cds_text_to_5x:
        raise AivwError(
            "approved dbAccess and cdsTextTo5x paths could not be qualified"
        )
    result = run_systemverilog_text_view_probe(
        profile,
        resolve_launch_paths(),
        environment=environment,
        cds_text_to_5x=cds_text_to_5x,
        dbaccess=dbaccess,
        timeout=args.timeout,
    )
    if args.json:
        print_json(result)
    else:
        print(f"status: {result['status']}")
        print(f"run_dir: {result['run_dir']}")
        print(f"payload_dir: {result['details']['payload_root']}")
        print(f"target: {result['details']['target']}")
    return 0 if result["status"] == "PASS" else 1
