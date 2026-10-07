"""Evaluate candidate contracts and construct deterministic revision metadata."""

from __future__ import annotations








import re


from typing import Any, Callable, Mapping




from .protocol import (
    Action,
    ProtocolError,
)




from .runtime import AgentState, RuntimeConfig


from .qualification_contract import QUALIFICATION_BLOCKED_PROVIDER, QUALIFICATION_FAIL_REVISION, QUALIFICATION_PASS, _DIGEST, _PROVIDER_FAILURE_CODES, _assert_no_forbidden_verdict, _digest
from .qualification_models import CandidateRevision, GateFeedback, ProviderExchange, QualificationReport

GateEvaluator = Callable[[Mapping[str, Any], Mapping[str, Any]], GateFeedback]


def default_candidate_gate(
    candidate: Mapping[str, Any],
    context: Mapping[str, Any],
) -> GateFeedback:
    """Conservative deterministic candidate contract gate.

    This is intentionally a structural/safety gate, not an analog-quality
    oracle.  It rejects malformed or stale candidates and leaves numerical
    quality to the LDO deterministic metrics gates.
    """

    if not isinstance(candidate, Mapping):
        return GateFeedback("BLOCKED", "candidate_not_object", {}, {})
    try:
        _assert_no_forbidden_verdict(candidate, "candidate")
    except ProtocolError:
        return GateFeedback("BLOCKED", "provider_verdict_forbidden", {}, {})
    source = candidate.get("source")
    if not isinstance(source, str) or not source.strip():
        return GateFeedback("FAIL", "candidate_source_missing", {}, {})
    if len(source.encode("utf-8")) > 512 * 1024:
        return GateFeedback("FAIL", "candidate_size_exceeded", {}, {})
    expected_generation = context.get("source_generation")
    if expected_generation is not None and candidate.get("source_generation") not in (None, expected_generation):
        return GateFeedback("FAIL", "candidate_source_generation_mismatch", {}, {"expected": expected_generation})
    expected_lock = context.get("template_lock")
    if expected_lock is not None and candidate.get("template_lock") not in (None, expected_lock):
        return GateFeedback("FAIL", "candidate_template_lock_mismatch", {}, {"expected": expected_lock})
    module = candidate.get("module") or candidate.get("module_name")
    if isinstance(module, str) and module and not re.search(r"\bmodule\s+%s\b" % re.escape(module), source):
        return GateFeedback("FAIL", "candidate_module_declaration_missing", {"module": module}, {})
    return GateFeedback("PASS", "candidate_contract_pass", {"source_bytes": len(source.encode("utf-8"))}, {})


def _candidate_from_action(action: Action) -> Mapping[str, Any] | None:
    value = action.params.get("candidate")
    return value if isinstance(value, Mapping) else None


def _candidate_summary(candidate: Mapping[str, Any] | None) -> dict[str, Any]:
    if candidate is None:
        return {"present": False}
    source = candidate.get("source")
    summary: dict[str, Any] = {"present": True}
    if isinstance(source, str):
        summary.update({"source_sha256": _digest(source), "source_bytes": len(source.encode("utf-8"))})
    for key in ("module", "module_name", "template_id", "template_version", "model_version"):
        value = candidate.get(key)
        if isinstance(value, (str, int, float, bool)):
            summary[key] = value
    return summary


def _revision_from_action(
    action: Action,
    *,
    revision: int,
    source_generation: str,
    template_lock: str | None,
    parent_sha256: str | None,
    context: Mapping[str, Any],
    feedback: GateFeedback | None,
    request_sha256: str | None = None,
    response_sha256: str | None = None,
    turn_id: str | None = None,
    session_id: str | None = None,
) -> CandidateRevision:
    candidate = _candidate_from_action(action)
    candidate_hash = _digest(candidate if candidate is not None else {"action": action.to_dict()})
    prompt_hash = action.params.get("prompt_hash")
    context_hash = action.params.get("context_hash")
    return CandidateRevision(
        revision=revision,
        action_id=action.action_id,
        action_kind=action.kind,
        candidate_sha256=candidate_hash,
        source_generation=source_generation,
        template_lock=template_lock,
        parent_sha256=parent_sha256,
        prompt_sha256=prompt_hash if isinstance(prompt_hash, str) and _DIGEST.fullmatch(prompt_hash) else None,
        context_sha256=context_hash if isinstance(context_hash, str) and _DIGEST.fullmatch(context_hash) else _digest(context),
        gate_feedback_sha256=None if feedback is None else feedback.sha256,
        gate_feedback=feedback,
        candidate_summary=_candidate_summary(candidate),
        request_sha256=request_sha256,
        response_sha256=response_sha256,
        turn_id=turn_id,
        session_id=session_id,
    )


def _strict_m2_runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        max_turns=8,
        max_tool_calls=8,
        max_context_bytes=64 * 1024,
        max_context_items=256,
        turn_timeout_seconds=10.0,
        total_timeout_seconds=60.0,
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


def _runtime_failure_status(result: Any) -> str:
    if result.last_error is not None and result.last_error.code in _PROVIDER_FAILURE_CODES:
        return QUALIFICATION_BLOCKED_PROVIDER
    if result.state == AgentState.TIMEOUT:
        return QUALIFICATION_BLOCKED_PROVIDER
    return QUALIFICATION_FAIL_REVISION


def _merge_workflow_fallback(
    primary: QualificationReport,
    fallback: QualificationReport,
) -> QualificationReport:
    # A fallback is an explicitly separate qualification attempt, never a
    # hidden retry.  Retain both complete certificates and renumber the flat
    # exchange view so its indexes remain unique.
    exchanges: list[ProviderExchange] = list(primary.exchanges)
    for offset, item in enumerate(fallback.exchanges, start=len(exchanges)):
        value = item.to_dict()
        value["index"] = offset
        exchanges.append(ProviderExchange(**value))
    return QualificationReport(
        provider=primary.provider,
        protocol_version=primary.protocol_version,
        runtime_identity=fallback.runtime_identity,
        source_generation=primary.source_generation,
        template_lock=primary.template_lock,
        status=QUALIFICATION_PASS if fallback.passed else primary.status,
        exchanges=tuple(exchanges),
        revisions=primary.revisions + fallback.revisions,
        fallback_provider=fallback.provider,
        errors=primary.errors + fallback.errors,
        metadata={
            "explicit_fallback": True,
            "primary": primary.to_dict(),
            "fallback": fallback.to_dict(),
        },
        started_at=primary.started_at,
        finished_at=fallback.finished_at,
    )

