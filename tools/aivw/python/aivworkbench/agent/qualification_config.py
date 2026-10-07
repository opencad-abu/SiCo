"""Issue an offline provider configuration certificate."""

from __future__ import annotations










from typing import Any, Iterable, Mapping










from .qualification_contract import QUALIFICATION_BLOCKED_INPUT, QUALIFICATION_CONFIG_VALIDATED, _now, _provider_identity
from .qualification_models import QualificationReport

def configuration_validation_report(
    provider: object,
    *,
    source_generation: str = "config-only",
    template_lock: str | None = None,
    runtime_identity: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    errors: Iterable[Mapping[str, Any]] = (),
) -> QualificationReport:
    """Create an offline provider-configuration certificate.

    ``CONFIG_VALIDATED`` is deliberately not ``PASS``: it proves only that a
    provider object and its security policy were constructed.  A live model
    call must go through :func:`qualify_provider` and receive ``PASS`` with
    request/response evidence.  Keeping this status in the same private report
    schema makes dry-run configuration checks auditable without implying model
    availability.
    """

    if not isinstance(source_generation, str) or not source_generation:
        source_generation = "config-only"
    safe_errors = tuple(errors)
    status = QUALIFICATION_CONFIG_VALIDATED if not safe_errors else QUALIFICATION_BLOCKED_INPUT
    return QualificationReport(
        provider=_provider_identity(provider) if provider is not None else {"name": "unconfigured", "version": "0"},
        protocol_version="aivw-agent-v1",
        runtime_identity=dict(runtime_identity or {}),
        source_generation=source_generation,
        template_lock=template_lock,
        status=status,
        errors=safe_errors,
        metadata={"configuration_only": True, **dict(metadata or {})},
        started_at=_now(),
        finished_at=_now(),
    )

