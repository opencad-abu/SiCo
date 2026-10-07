"""LDO qualification and exploratory plan command adapters."""

from __future__ import annotations

import argparse
from pathlib import Path
from .errors import AivwError
from .profiles import load_profile
from .workspace import resolve_launch_paths
from .cli_output import print_json


def qualify_ldo(args: argparse.Namespace) -> int:
    from .ldo_qualification import qualify_ldo_profile

    report = qualify_ldo_profile(load_profile(args.profile))
    if args.json:
        print_json(report)
    else:
        print(f"status: {report['status']}")
        snapshot = report.get("source_snapshot", {})
        if isinstance(snapshot, dict):
            print(f"source_root: {snapshot.get('source_root', '')}")
            for finding in snapshot.get("errors", ()):
                print(f"finding: {finding}")
        for finding in report.get("findings", ()):
            print(f"finding: {finding}")
    return 0 if report["status"] in {"PASS", "ANALOG_ISLAND", "QUALIFIED"} else 1


def qualify_ldo_live(args: argparse.Namespace) -> int:
    from .ldo_live import run_ldo_live
    from .toolchain import capture_module_environment, probe_tools

    report = run_ldo_live(
        load_profile(args.profile),
        resolve_launch_paths(),
        timeout=args.timeout,
        environment_loader=capture_module_environment,
        tool_prober=probe_tools,
        runams=not args.no_runams,
    )
    if args.json:
        print_json(report)
    else:
        print(f"status: {report['status']}")
        print(f"run_dir: {report['run_dir']}")
        print(f"payload_dir: {report['payload_dir']}")
        details = report.get("details", {})
        if isinstance(details, dict):
            for finding in details.get("findings", ()):
                print(f"finding: {finding}")
    return 0 if report["status"] == "PASS" else 1


def plan_ldo_exploration(args: argparse.Namespace) -> int:
    from .ldo_exploratory import build_exploratory_plan, read_exploratory_policy

    policy_path = Path(args.policy).expanduser().resolve()
    policy = read_exploratory_policy(policy_path)
    plan = build_exploratory_plan(policy)
    if args.output:
        output = Path(args.output).expanduser().resolve()
        if not output.is_absolute():
            raise AivwError("exploration output must be an absolute path")
        output.parent.mkdir(parents=True, exist_ok=True)
        from .workspace import write_json_once

        write_json_once(output, plan)
        plan["output"] = str(output)
    if args.json:
        print_json(plan)
    else:
        print(f"status: {plan['tolerance_contract']['status']}")
        print(f"topology: {plan['topology']}")
        print(f"cases: {len(plan['cases'])}")
        print(f"behavior_verdict: {plan['behavior_verdict']}")
        print(f"correlation_status: {plan['correlation_status']}")
    return 0
