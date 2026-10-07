"""External provider qualification report presentation."""

from __future__ import annotations
import argparse
from .agent.qualification import (
    QUALIFICATION_CONFIG_VALIDATED,
    configuration_validation_report,
)
from .cli_report import write_report
from .cli_output import print_json


def configuration_failure(
    args: argparse.Namespace, message: str, *, code: str = "INVALID_ARGUMENTS"
) -> int:
    """Persist a safe preflight failure when provider construction fails.

    A rejected endpoint or socket is still useful qualification evidence:
    the report records that no live call was attempted, without retaining
    the raw configuration or a possible credential in the error text.
    """

    # Do not echo rejected provenance values into a report.  In addition
    # to avoiding malformed QualificationReport instances, this prevents
    # a caller from smuggling a credential-like string into an observable
    # error path.  The failure itself is still attributable through the
    # report status and stable error code.
    safe_source = "config-only"
    safe_lock = None
    report = configuration_validation_report(
        None,
        source_generation=safe_source,
        template_lock=safe_lock,
        runtime_identity={
            "cli": "qualify-external-provider",
            "live": bool(args.live),
            "approved": bool(args.approved),
            "approval_id": args.approval_id if args.live else None,
        },
        metadata={
            "configuration_only": not bool(args.live),
            "provider_kind": args.provider,
            "live_calls": 0,
        },
        errors=({"code": code, "message": message},),
    )
    report_path = write_report(report, args)
    payload = report.to_dict()
    payload["report_path"] = str(report_path)
    if args.json:
        print_json(payload)
    else:
        print("status: %s" % report.status)
        print("report: %s" % report_path)
    return 1


def emit_report(args: argparse.Namespace, report: object) -> int:
    report_path = write_report(report, args)
    payload = report.to_dict()
    payload["report_path"] = str(report_path)
    if args.json:
        print_json(payload)
    else:
        print("status: %s" % report.status)
        print("report: %s" % report_path)
    return (
        0 if report.status in {QUALIFICATION_CONFIG_VALIDATED} or report.passed else 1
    )
