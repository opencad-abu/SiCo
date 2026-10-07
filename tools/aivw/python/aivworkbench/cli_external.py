"""Config-only or explicitly authorized external provider qualification."""

from __future__ import annotations
import argparse
import hashlib
from .errors import AivwError
from .cli_external_validation import validate_external_arguments
from .cli_external_report import configuration_failure, emit_report
from .cli_fixtures import load_fixture, load_provider_requests
from .cli_report import write_report
from .cli_output import print_json


def qualify_external_provider(args: argparse.Namespace) -> int:
    """Validate or explicitly exercise one configured external provider.

    Construction and policy checks are always offline.  A provider call is
    reachable only when the operator supplies both ``--live`` and
    ``--approved`` (plus a non-secret approval identifier and request fixture).
    This keeps a copied command line or a dry-run from unexpectedly sending a
    model request to a network or local gateway.
    """

    from .agent.providers.http_model import HttpModelProvider, SecretReference
    from .agent.providers.unix_socket import UnixSocketProvider
    from .agent.qualification import (
        configuration_validation_report,
        qualify_provider,
    )

    validate_external_arguments(args)
    if args.provider == "http":
        if not args.endpoint:
            raise AivwError("--endpoint is required with --provider http")
        if args.socket_path or args.managed_roots:
            raise AivwError(
                "--socket/--managed-root are only valid with --provider unix"
            )
        if not args.allowed_hosts:
            raise AivwError(
                "at least one --allowed-host is required with --provider http"
            )
        try:
            secret_reference = SecretReference.from_value(args.api_key_env)
            provider = HttpModelProvider(
                args.endpoint,
                allowed_hosts=set(args.allowed_hosts),
                timeout_seconds=float(args.timeout),
                max_response_bytes=args.max_response_bytes,
                secret_reference=secret_reference,
                model_id=args.model_id,
            )
        except (TypeError, ValueError):
            return configuration_failure(
                args, "HTTP provider configuration was rejected"
            )
        provider_metadata = {
            "transport": "https",
            "endpoint_digest": hashlib.sha256(
                provider.config.endpoint.encode("utf-8")
            ).hexdigest(),
            "secret_reference": provider.secret_reference,
            "allowed_hosts": sorted(provider.config.allowed_hosts),
            "model_id": provider.model_id,
        }
    else:
        if args.endpoint or args.allowed_hosts or args.api_key_env:
            raise AivwError(
                "--endpoint/--allowed-host/--api-key-env are only valid with --provider http"
            )
        if not args.socket_path:
            raise AivwError("--socket is required with --provider unix")
        if not args.managed_roots:
            raise AivwError(
                "at least one --managed-root is required with --provider unix"
            )
        try:
            provider = UnixSocketProvider(
                args.socket_path,
                managed_roots=tuple(args.managed_roots),
                timeout_seconds=float(args.timeout),
                max_response_bytes=args.max_response_bytes,
                model_id=args.model_id,
            )
        except (TypeError, ValueError):
            return configuration_failure(
                args, "Unix provider configuration was rejected"
            )
        socket_is_managed = bool(provider.socket_is_managed)
        provider_metadata = {
            "transport": "af_unix",
            "socket_path_digest": hashlib.sha256(
                str(provider.socket_path).encode("utf-8")
            ).hexdigest(),
            "managed_root_digests": [
                hashlib.sha256(str(item).encode("utf-8")).hexdigest()
                for item in provider.managed_roots
            ],
            "socket_is_managed": socket_is_managed,
            "model_id": provider.model_id,
        }

    runtime_identity = {
        "cli": "qualify-external-provider",
        "live": bool(args.live),
        "approved": bool(args.approved),
        "approval_id": args.approval_id if args.live else None,
    }
    if not args.live:
        config_errors = ()
        if args.provider == "unix" and not provider_metadata.get("socket_is_managed"):
            config_errors = (
                {
                    "code": "MODEL_PROVIDER_UNAVAILABLE",
                    "message": "managed Unix socket is not currently available",
                },
            )
        report = configuration_validation_report(
            provider,
            source_generation=args.source_generation or "config-only",
            template_lock=args.template_lock,
            runtime_identity=runtime_identity,
            metadata={**provider_metadata, "live_calls": 0},
            errors=config_errors,
        )
    else:
        if args.candidate_workflow:
            from .agent.qualification import run_candidate_revision_workflow
            from .agent.runtime import RuntimeConfig

            raw_context = load_fixture(args.candidate_context, "candidate context")
            if not isinstance(raw_context, dict):
                raise AivwError("candidate context fixture must contain an object")
            # Candidate workflow uses the same strict bounded profile as the
            # offline revision command.  The provider itself remains the only
            # source of actions; deterministic gate feedback is controller
            # owned and never accepted from provider metadata.
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
            workflow = run_candidate_revision_workflow(
                provider,
                source_generation=args.source_generation,
                template_lock=args.template_lock,
                initial_context=raw_context,
                config=config,
                run_id="m2-external-provider-qualification",
                expected_model_id=args.model_id,
                runtime_identity=runtime_identity,
                metadata={**provider_metadata, "live_calls": "bounded_workflow"},
            )
            report = workflow.report
            payload = workflow.to_dict()
            report_path = write_report(report, args)
            payload["report_path"] = str(report_path)
            if args.json:
                print_json(payload)
            else:
                print("status: %s" % report.status)
                print("runtime: %s" % workflow.runtime.status)
                print("report: %s" % report_path)
            return 0 if report.passed else 1
        if args.requests is None or not str(args.requests).strip():
            raise AivwError("--requests is required with --live --approved")
        requests = load_provider_requests(args.requests)
        report = qualify_provider(
            provider,
            requests,
            expected_action_kinds=args.expected_action_kinds or None,
            source_generation=args.source_generation,
            template_lock=args.template_lock,
            runtime_identity=runtime_identity,
            metadata={**provider_metadata, "live_calls": len(requests)},
            call_timeout_seconds=float(args.timeout),
            expected_model_id=args.model_id,
        )
    return emit_report(args, report)
