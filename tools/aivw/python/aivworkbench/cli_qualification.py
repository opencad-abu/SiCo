"""Offline provider and candidate revision qualification commands."""

from __future__ import annotations

import argparse
import math
from .errors import AivwError
from .cli_fixtures import load_fixture, load_provider_requests, load_replay_records
from .cli_report import write_report
from .cli_output import print_json


def qualify_provider(args: argparse.Namespace) -> int:
    from .agent.qualification import (
        qualify_bundle_provider,
        qualify_replay_provider,
    )

    if (
        not isinstance(args.timeout, (int, float))
        or isinstance(args.timeout, bool)
        or not math.isfinite(args.timeout)
        or args.timeout <= 0
    ):
        raise AivwError("--timeout must be a positive finite number")
    if args.provider == "replay":
        if args.bundle:
            raise AivwError("--bundle is only valid with --provider bundle")
        if not args.records:
            raise AivwError("--records is required with --provider replay")
        records = load_replay_records(args.records)
        requests = load_provider_requests(args.requests)
        report = qualify_replay_provider(
            records,
            requests,
            expected_action_kinds=args.expected_action_kinds or None,
            source_generation=args.source_generation,
            template_lock=args.template_lock,
            runtime_identity={"cli": "qualify-provider", "offline": True},
            call_timeout_seconds=args.timeout,
        )
    else:
        if args.records:
            raise AivwError("--records is only valid with --provider replay")
        if not args.bundle:
            raise AivwError("--bundle is required with --provider bundle")
        requests = load_provider_requests(args.requests)
        report = qualify_bundle_provider(
            args.bundle,
            requests,
            expected_action_kinds=args.expected_action_kinds or None,
            source_generation=args.source_generation,
            template_lock=args.template_lock,
            runtime_identity={"cli": "qualify-provider", "offline": True},
            call_timeout_seconds=args.timeout,
        )
    report_path = write_report(report, args)
    payload = report.to_dict()
    payload["report_path"] = str(report_path)
    if args.json:
        print_json(payload)
    else:
        print("status: %s" % report.status)
        print("report: %s" % report_path)
    return 0 if report.passed else 1


def qualify_revision(args: argparse.Namespace) -> int:
    from .agent.providers.replay import ReplayProvider
    from .agent.providers.rule_template import RuleTemplateProvider
    from .agent.qualification import run_candidate_revision_workflow
    from .agent.runtime import RuntimeConfig

    if (
        not isinstance(args.timeout, (int, float))
        or isinstance(args.timeout, bool)
        or not math.isfinite(args.timeout)
        or args.timeout <= 0
    ):
        raise AivwError("--timeout must be a positive finite number")
    raw_context = load_fixture(args.context, "context")
    if not isinstance(raw_context, dict):
        raise AivwError("context fixture must contain an object")
    if args.provider == "rule":
        if args.records:
            raise AivwError("--records is only valid with --provider replay")
        provider = RuleTemplateProvider.for_ldo(template_lock=args.template_lock)
    else:
        if not args.records:
            raise AivwError("--records is required with --provider replay")
        provider = ReplayProvider(load_replay_records(args.records))
    # Keep the CLI's timeout a runtime boundary while preserving the strict M2
    # action budget contract.  The provider remains the only source of actions.
    config = RuntimeConfig(
        max_turns=8,
        max_tool_calls=8,
        max_context_bytes=64 * 1024,
        max_context_items=256,
        turn_timeout_seconds=float(args.timeout),
        total_timeout_seconds=max(60.0, float(args.timeout) * 8.0),
        required_action_budget_fields=frozenset(
            {"tokens", "simulation_cases", "simulation_seconds"}
        ),
        max_action_tokens=100_000,
        max_action_simulation_cases=256,
        max_action_simulation_seconds=300.0,
        max_total_action_tokens=400_000,
        max_total_simulation_cases=1_024,
        max_total_simulation_seconds=1_200.0,
        fsync_events=False,
    )
    try:
        result = run_candidate_revision_workflow(
            provider,
            source_generation=args.source_generation,
            template_lock=args.template_lock,
            initial_context=raw_context,
            config=config,
            run_id="m2-cli-qualification",
        )
    except ValueError as exc:
        raise AivwError("revision qualification failed: %s" % exc) from exc
    report_path = write_report(result.report, args)
    payload = result.to_dict()
    payload["report_path"] = str(report_path)
    if args.json:
        print_json(payload)
    else:
        print("status: %s" % result.report.status)
        print("runtime: %s" % result.runtime.status)
        print("report: %s" % report_path)
    return 0 if result.report.passed else 1
