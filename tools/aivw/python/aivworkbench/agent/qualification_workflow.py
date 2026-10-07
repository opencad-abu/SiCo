"""Run the bounded candidate, gate feedback and revision workflow."""

from __future__ import annotations










from typing import Any, Callable, Mapping

from .backend import (
    AgentProvider,
)


from .policy import AgentPolicy

from .protocol import (
    Action,
    ActionKind,
    ErrorCode,
    ProtocolError,
)

from .value_codec import thaw



from .runtime import AgentRuntime, AgentState, RuntimeConfig

from .tool_broker import ToolBroker, ToolResult

from .qualification_contract import QUALIFICATION_FAIL_REVISION, QUALIFICATION_PASS, _digest, _now, _provider_identity, _validate_generation
from .qualification_models import CandidateRevision, GateFeedback, QualificationReport, RevisionWorkflowResult, _RecordingProvider
from .qualification_exchange import _action_from_response, _exchange_error, _exchange_from_call
from .qualification_acceptance import _runtime_action_acceptance
from .qualification_candidate import GateEvaluator, _merge_workflow_fallback, _revision_from_action, _runtime_failure_status, _strict_m2_runtime_config, default_candidate_gate

def run_candidate_revision_workflow(
    provider: AgentProvider,
    *,
    source_generation: str,
    template_lock: str | None = None,
    initial_context: Mapping[str, Any] | None = None,
    config: RuntimeConfig | None = None,
    policy: AgentPolicy | None = None,
    broker: ToolBroker | None = None,
    gate_evaluator: GateEvaluator | None = None,
    fallback_provider: AgentProvider | None = None,
    run_id: str = "m2-qualification",
    expected_model_id: str | None = None,
    runtime_identity: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> RevisionWorkflowResult:
    """Run one bounded candidate -> gate feedback -> revision workflow.

    ``provider`` remains passive.  The tool harness records candidate
    revisions and emits deterministic feedback in ``previous_result`` for the
    next provider turn.  A final ``AgentRuntime.evaluate_gate`` call is made
    only from the deterministic feedback, never from a provider-provided
    verdict.
    """

    _validate_generation(source_generation, "source_generation")
    if expected_model_id is not None and (
        not isinstance(expected_model_id, str)
        or not expected_model_id
        or len(expected_model_id) > 256
        or any(char.isspace() or ord(char) < 0x20 for char in expected_model_id)
    ):
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "expected_model_id is invalid")
    if template_lock is not None:
        _validate_generation(template_lock, "template_lock")
    if not isinstance(initial_context, (Mapping, type(None))):
        raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "initial_context must be an object")
    context = dict(initial_context or {})
    declared_source = context.get("source_generation")
    if declared_source is not None and declared_source != source_generation:
        raise ProtocolError(
            ErrorCode.STALE_SOURCE_GENERATION,
            "initial context source_generation does not match the active source",
        )
    context["source_generation"] = source_generation
    if template_lock is not None:
        declared_lock = context.get("template_lock")
        if declared_lock is not None and declared_lock != template_lock:
            raise ProtocolError(
                ErrorCode.TEMPLATE_LOCK_MISMATCH,
                "initial context template_lock does not match the active lock",
            )
        context["template_lock"] = template_lock
    elif context.get("template_lock") is not None:
        raise ProtocolError(
            ErrorCode.TEMPLATE_LOCK_MISMATCH,
            "initial context declares a template_lock but the run has no active lock",
        )
    active_policy = policy or AgentPolicy.restrictive(
        tools=["plan_experiment", "submit_experiment", "propose_model", "request_revision"]
    )
    active_broker = broker or ToolBroker(
        active_policy,
        source_generation=source_generation,
        template_lock=template_lock,
    )
    gate = gate_evaluator or default_candidate_gate
    # The handler records the exact action object before evaluating the
    # candidate.  This preserves real action_id/kind/digest provenance while
    # the runtime remains authoritative for validation and side effects.
    action_feedback: dict[str, GateFeedback] = {}
    existing_tools = {item["name"] for item in active_broker.describe()}

    def generic_handler(name: str) -> Callable[[Mapping[str, Any]], ToolResult]:
        def handle(params: Mapping[str, Any]) -> ToolResult:
            feedback: GateFeedback | None = None
            if name in {"propose_model", "request_revision"}:
                candidate = params.get("candidate")
                feedback = (
                    gate(
                        candidate,
                        {
                            **context,
                            "source_generation": source_generation,
                            "template_lock": template_lock,
                        },
                    )
                    if isinstance(candidate, Mapping)
                    else GateFeedback("BLOCKED", "candidate_missing", {}, {})
                )
            outputs: dict[str, Any] = {
                "tool": name,
                "accepted": True,
                "measurement_summary": context.get("measurement_summary", {}),
            }
            if feedback is not None:
                outputs["gate_feedback"] = feedback.to_dict()
                outputs["candidate_sha256"] = _digest(params.get("candidate", {}))
            return ToolResult(
                status="PASS",
                summary={"tool": name},
                outputs=outputs,
                source_generation=source_generation,
                side_effect=True,
                deterministic_gate=None if feedback is None else feedback.status,
            )

        return handle

    for name in ("plan_experiment", "submit_experiment", "propose_model", "request_revision"):
        if name not in existing_tools:
            active_broker.register_tool(
                name,
                generic_handler(name),
                schema={"type": "object", "additionalProperties": True},
                side_effect=True,
            )

    runtime_config = config or _strict_m2_runtime_config()
    recorder = _RecordingProvider(provider, expected_model_id=expected_model_id)
    runtime = AgentRuntime(
        recorder,
        active_broker,
        run_id=run_id,
        source_generation=source_generation,
        template_lock=template_lock,
        config=runtime_config,
        context_fragments=(context,),
        metadata={"qualification_scope": "M2_provider_candidate_revision"},
    )
    started = _now()
    result = runtime.run(initial_context=context)
    runtime_acceptance = _runtime_action_acceptance(result.events)
    exchanges = tuple(
        _exchange_from_call(
            call,
            index,
            provider,
            runtime_action_accepted=(
                runtime_acceptance.get(action.action_id)
                if (action := _action_from_response(call.response)) is not None
                else False
            ),
        )
        for index, call in enumerate(recorder.calls)
    )
    # Map deterministic feedback from the broker's persisted result to the
    # exact provider action that caused it.  No candidate action can complete
    # without a broker record, and terminal actions have no broker result.
    for raw in active_broker.export_results().values():
        if not isinstance(raw, Mapping) or not isinstance(raw.get("action"), Mapping):
            continue
        try:
            action = Action.from_dict(raw["action"])
        except ProtocolError:
            continue
        raw_result = raw.get("result")
        outputs = raw_result.get("outputs") if isinstance(raw_result, Mapping) else None
        raw_feedback = outputs.get("gate_feedback") if isinstance(outputs, Mapping) else None
        if not isinstance(raw_feedback, Mapping):
            continue
        try:
            feedback = GateFeedback(
                status=str(raw_feedback.get("status")),
                code=str(raw_feedback.get("code")),
                summary=raw_feedback.get("summary", {}),
                evidence=raw_feedback.get("evidence", {}),
            )
        except ProtocolError:
            continue
        if raw_feedback.get("sha256") == feedback.sha256:
            action_feedback[action.action_id] = feedback

    revisions: list[CandidateRevision] = []
    parent: str | None = None
    for index, call in enumerate(recorder.calls):
        action = _action_from_response(call.response)
        if action is None or action.kind not in {
            ActionKind.PROPOSE_MODEL.value,
            ActionKind.REQUEST_REVISION.value,
        }:
            continue
        exchange = exchanges[index]
        feedback = action_feedback.get(action.action_id)
        revision = _revision_from_action(
            action,
            revision=len(revisions),
            source_generation=source_generation,
            template_lock=template_lock,
            parent_sha256=parent,
            context=call.request.context,
            feedback=feedback,
            request_sha256=exchange.request_sha256,
            response_sha256=exchange.response_sha256,
            turn_id=call.request.turn_id,
            session_id=call.session_id,
        )
        revisions.append(revision)
        parent = revision.candidate_sha256

    final_feedback = next(
        (item.gate_feedback for item in reversed(revisions) if item.gate_feedback is not None),
        None,
    )
    # A provider FINISH is only a lifecycle signal.  PASS is established from
    # the deterministic candidate gate, then recorded on the runtime after it
    # has finished.  A failed last candidate remains a revision failure even
    # if the provider attempted to FINISH.
    if (
        result.state == AgentState.FINISHED
        and final_feedback is not None
        and final_feedback.status == "PASS"
    ):
        runtime.evaluate_gate(
            "QUALIFIED",
            gate="m2_candidate_contract",
            evidence={"feedback_sha256": final_feedback.sha256},
        )
        result = runtime.result()
        status = QUALIFICATION_PASS
    elif result.state in {
        AgentState.BLOCKED,
        AgentState.TIMEOUT,
        AgentState.UNKNOWN_SIDE_EFFECT,
    }:
        status = _runtime_failure_status(result)
    else:
        status = QUALIFICATION_FAIL_REVISION

    errors: list[Mapping[str, Any]] = []
    if recorder.start_error is not None:
        errors.append(_exchange_error(recorder.start_error))
    if recorder.resume_error is not None:
        errors.append(_exchange_error(recorder.resume_error))
    errors.extend(
        item.error for item in exchanges if item.error is not None
    )
    if result.last_error is not None:
        errors.append(result.last_error.to_dict())
    finished = _now()
    report = QualificationReport(
        provider=_provider_identity(provider),
        protocol_version="aivw-agent-v1",
        runtime_identity={
            "runtime": "aivw-agent-runtime",
            "state": result.state,
            "run_id": result.run_id,
            **dict(runtime_identity or {}),
        },
        source_generation=source_generation,
        template_lock=template_lock,
        status=status,
        exchanges=exchanges,
        revisions=tuple(revisions),
        errors=tuple(errors),
        metadata={
            "scope": "M2_provider_candidate_revision",
            "runtime_status": result.status,
            "runtime_state": result.state,
            "turns": result.turns,
            "tool_calls": result.tool_calls,
            "action_budget_used": result.checkpoint.get("action_budget_used", {}),
            "deterministic_gate_authority": True,
            "provider_finish_is_verdict": False,
            **dict(metadata or {}),
        },
        started_at=started,
        finished_at=finished,
    )
    fallback_not_attempted_reason: str | None = None
    effective_result = result
    if status != QUALIFICATION_PASS and fallback_provider is not None:
        # Once a mutating boundary is unknown, rerunning the same mutation
        # with another provider could duplicate an external effect.  Recovery
        # must be explicit and human-directed in that case; provider outage
        # and deterministic revision failures remain eligible for the caller's
        # explicitly selected fallback.
        if result.state == AgentState.UNKNOWN_SIDE_EFFECT:
            fallback_not_attempted_reason = "unknown_side_effect"
        else:
            fallback_result = run_candidate_revision_workflow(
                fallback_provider,
                source_generation=source_generation,
                template_lock=template_lock,
                initial_context=context,
                config=runtime_config,
                gate_evaluator=gate,
                run_id=run_id + "-fallback",
            )
            report = _merge_workflow_fallback(report, fallback_result.report)
            effective_result = fallback_result.runtime
    if fallback_not_attempted_reason is not None:
        report = QualificationReport(
            provider=report.provider,
            protocol_version=report.protocol_version,
            runtime_identity=report.runtime_identity,
            source_generation=report.source_generation,
            template_lock=report.template_lock,
            status=report.status,
            exchanges=report.exchanges,
            revisions=report.revisions,
            fallback_provider=None,
            errors=report.errors,
            metadata={
                **thaw(report.metadata),
                "fallback_not_attempted_reason": fallback_not_attempted_reason,
            },
            started_at=report.started_at,
            finished_at=report.finished_at,
        )
    return RevisionWorkflowResult(runtime=effective_result, report=report)

