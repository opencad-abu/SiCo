"""Preflight argument checks for external provider qualification."""

from __future__ import annotations

import argparse
import math
import re
from .errors import AivwError


_APPROVAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,255}$")


_APPROVAL_ID_SENSITIVE_PREFIXES = (
    "sk-",
    "sk_",
    "key-",
    "key_",
    "api-key-",
    "api_key-",
    "access-token-",
    "access_token-",
    "authorization-",
    "password-",
    "passwd-",
    "private-key-",
    "private_key-",
    "secret-",
    "secret_",
    "token-",
    "token_",
    "bearer:",
)


_MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@=-]{0,255}$")


def validate_approval_id(value: object) -> bool:
    """Return whether *value* is a non-sensitive change reference."""

    if not isinstance(value, str) or _APPROVAL_ID_PATTERN.fullmatch(value) is None:
        return False
    lowered = value.casefold()
    return not lowered.startswith(_APPROVAL_ID_SENSITIVE_PREFIXES)


def validate_model_id(value: object) -> bool:
    """Return whether *value* is a bounded, non-secret model identity."""

    return isinstance(value, str) and _MODEL_ID_PATTERN.fullmatch(value) is not None


def validate_external_arguments(args: argparse.Namespace) -> None:
    if (
        not isinstance(args.timeout, (int, float))
        or isinstance(args.timeout, bool)
        or not math.isfinite(float(args.timeout))
        or float(args.timeout) <= 0
    ):
        raise AivwError("--timeout must be a positive finite number")
    if (
        not isinstance(args.max_response_bytes, int)
        or isinstance(args.max_response_bytes, bool)
        or args.max_response_bytes <= 0
    ):
        raise AivwError("--max-response-bytes must be a positive integer")
    if args.live != args.approved:
        raise AivwError("--live and --approved must be supplied together")
    if args.live and (not args.approval_id or not isinstance(args.approval_id, str)):
        raise AivwError("--approval-id is required with --live --approved")
    if args.approval_id is not None and not validate_approval_id(args.approval_id):
        raise AivwError(
            "--approval-id must be a non-sensitive ASCII change reference "
            "(letters, digits, . _ : / @ + -; at most 256 characters)"
        )
    if args.model_id is not None and not validate_model_id(args.model_id):
        raise AivwError(
            "--model-id must be a non-secret ASCII identity (at most 256 characters)"
        )
    # A model identity is harmless provenance in config-only mode.  It is
    # retained in the preflight report and, when live, becomes an exact
    # response binding below.
    if args.candidate_workflow and not args.live:
        raise AivwError("--candidate-workflow is only valid with --live --approved")
    if args.candidate_workflow and not args.candidate_context:
        raise AivwError("--candidate-context is required with --candidate-workflow")
    if not args.candidate_workflow and args.candidate_context is not None:
        raise AivwError("--candidate-context is only valid with --candidate-workflow")
    if not args.live:
        if args.requests is not None:
            raise AivwError("--requests is only valid with --live --approved")
        if args.approval_id is not None:
            raise AivwError("--approval-id is only valid with --live --approved")
        if args.expected_action_kinds:
            raise AivwError(
                "--expected-action-kind is only valid with --live --approved"
            )
        if args.source_generation is not None:
            raise AivwError("--source-generation is only valid with --live --approved")
        if args.template_lock is not None:
            raise AivwError("--template-lock is only valid with --live --approved")
    elif (
        not isinstance(args.source_generation, str)
        or not args.source_generation.strip()
    ):
        raise AivwError("--source-generation is required with --live --approved")
