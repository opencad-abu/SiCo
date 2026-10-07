"""Immutable provider exchange, gate feedback, revision and report models."""

from __future__ import annotations

from dataclasses import dataclass, field








import threading

from typing import Any, Iterable, Mapping

from .backend import (
    AgentProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderSession,
)



from .protocol import (
    Action,
    ActionKind,
    ErrorCode,
    ProtocolError,
)

from .value_codec import freeze, thaw





from .qualification_contract import QUALIFICATION_PASS, QUALIFICATION_SCHEMA_VERSION, QUALIFICATION_STATUSES, _DIGEST, _assert_no_forbidden_verdict, _bounded, _digest, _validate_generation, _validate_template_lock

@dataclass
class _ProviderCall:
    """Private, bounded provenance captured at the provider boundary."""

    request: ProviderRequest
    response: ProviderResponse | Action | object | None = None
    error: BaseException | None = None
    session_id: str | None = None


class _RecordingProvider:
    """Delegate a provider while retaining every real request/response pair.

    ``AgentRuntime`` owns all lifecycle and timeout semantics.  This adapter is
    intentionally transparent: it only records the immutable request and the
    returned typed object (or exception), and never retries or changes the
    provider result.  It is used for qualification evidence, not production
    dispatch.
    """

    def __init__(self, provider: AgentProvider, *, expected_model_id: str | None = None) -> None:
        self.provider = provider
        self.name = getattr(provider, "name", type(provider).__name__)
        self.version = getattr(provider, "version", "unspecified")
        self.calls: list[_ProviderCall] = []
        self.start_error: BaseException | None = None
        self.resume_error: BaseException | None = None
        self.session_id: str | None = None
        self.expected_model_id = expected_model_id
        self._lock = threading.RLock()

    def start(self, request: ProviderRequest) -> ProviderSession:
        try:
            session = self.provider.start(request)
        except BaseException as exc:
            with self._lock:
                self.start_error = exc
            raise
        with self._lock:
            self.session_id = session.session_id
        return session

    def resume(self, session: ProviderSession, request: ProviderRequest) -> ProviderSession:
        try:
            resumed = self.provider.resume(session, request)
        except BaseException as exc:
            with self._lock:
                self.resume_error = exc
            raise
        with self._lock:
            self.session_id = resumed.session_id
        return resumed

    def next_action(self, request: ProviderRequest) -> ProviderResponse | Action:
        call = _ProviderCall(request=request, session_id=self.session_id)
        try:
            response = self.provider.next_action(request)
        except BaseException as exc:
            call.error = exc
            with self._lock:
                self.calls.append(call)
            raise
        # Keep the typed response on the call even when a qualification-only
        # binding check rejects it below.  The runtime must not execute the
        # action, but the report still needs the response digest, provider,
        # model identity, usage, and metadata for forensic provenance.
        call.response = response
        # Model identity is an audited binding when the caller supplies an
        # expectation.  Reject a response before it reaches the runtime so a
        # provider cannot perform a useful action under an unqualified model.
        if self.expected_model_id is not None:
            actual = response.model_id if isinstance(response, ProviderResponse) else None
            if actual != self.expected_model_id:
                error = ProtocolError(
                    ErrorCode.INVALID_ACTION,
                    "provider model identity does not match qualification expectation",
                    {
                        "expected_model_id": self.expected_model_id,
                        "actual_model_id": actual,
                    },
                )
                call.error = error
                with self._lock:
                    self.calls.append(call)
                raise error
        with self._lock:
            self.calls.append(call)
        return response

    def interrupt(self, session: ProviderSession | None = None) -> None:
        return self.provider.interrupt(session)

    def events(self, session: ProviderSession | None = None) -> Iterable[Any]:
        return self.provider.events(session)

    def export_state(self) -> Mapping[str, Any]:
        return self.provider.export_state()

    def restore_state(self, value: Mapping[str, Any]) -> None:
        return self.provider.restore_state(value)


@dataclass(frozen=True)
class ProviderExchange:
    """One request/response pair (or a structured provider failure)."""

    index: int
    request_sha256: str
    response_sha256: str | None
    provider: str
    model_id: str | None
    status: str
    request: Mapping[str, Any]
    response: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None
    session_id: str | None = None
    # ``provider_response_valid`` describes only the provider boundary.  A
    # syntactically valid response can still be rejected by the runtime
    # policy, budget, generation, or template binding.  The optional runtime
    # flag keeps those two decisions auditable without changing the provider
    # wire contract.
    provider_response_valid: bool = True
    runtime_action_accepted: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.index, int) or isinstance(self.index, bool) or self.index < 0:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification exchange index is invalid")
        if not isinstance(self.request_sha256, str) or not _DIGEST.fullmatch(self.request_sha256):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification request digest is invalid")
        if self.response_sha256 is not None and (
            not isinstance(self.response_sha256, str) or not _DIGEST.fullmatch(self.response_sha256)
        ):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification response digest is invalid")
        if not isinstance(self.provider, str) or not self.provider:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification exchange provider is invalid")
        if self.model_id is not None and not isinstance(self.model_id, str):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification exchange model_id is invalid")
        if not isinstance(self.status, str) or not self.status:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification exchange status is invalid")
        if not isinstance(self.request, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification exchange request is invalid")
        if self.response is not None and not isinstance(self.response, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification exchange response is invalid")
        if self.error is not None and not isinstance(self.error, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification exchange error is invalid")
        if not isinstance(self.provider_response_valid, bool):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification provider_response_valid is invalid")
        if self.runtime_action_accepted is not None and not isinstance(self.runtime_action_accepted, bool):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification runtime_action_accepted is invalid")
        object.__setattr__(self, "request", freeze(_bounded(self.request)))
        object.__setattr__(self, "response", None if self.response is None else freeze(_bounded(self.response)))
        object.__setattr__(self, "error", None if self.error is None else freeze(_bounded(self.error)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "request_sha256": self.request_sha256,
            "response_sha256": self.response_sha256,
            "provider": self.provider,
            "model_id": self.model_id,
            "status": self.status,
            "request": thaw(self.request),
            "response": None if self.response is None else thaw(self.response),
            "error": None if self.error is None else thaw(self.error),
            "session_id": self.session_id,
            "provider_response_valid": self.provider_response_valid,
            "runtime_action_accepted": self.runtime_action_accepted,
        }


@dataclass(frozen=True)
class GateFeedback:
    """Deterministic checker feedback attached to a candidate revision."""

    status: str
    code: str
    summary: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if self.status not in {"PASS", "FAIL", "BLOCKED"}:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "gate feedback status is invalid")
        if not isinstance(self.code, str) or not self.code:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "gate feedback code is invalid")
        if not isinstance(self.summary, Mapping) or not isinstance(self.evidence, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "gate feedback payload must be objects")
        _assert_no_forbidden_verdict(self.summary, "summary")
        _assert_no_forbidden_verdict(self.evidence, "evidence")
        summary = freeze(_bounded(self.summary))
        evidence = freeze(_bounded(self.evidence))
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "sha256", _digest({"status": self.status, "code": self.code, "summary": thaw(summary), "evidence": thaw(evidence)}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "code": self.code,
            "summary": thaw(self.summary),
            "evidence": thaw(self.evidence),
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class CandidateRevision:
    """Immutable candidate generation/revision provenance."""

    revision: int
    action_id: str
    action_kind: str
    candidate_sha256: str
    source_generation: str
    template_lock: str | None
    parent_sha256: str | None = None
    prompt_sha256: str | None = None
    context_sha256: str | None = None
    gate_feedback_sha256: str | None = None
    gate_feedback: GateFeedback | None = None
    candidate_summary: Mapping[str, Any] = field(default_factory=dict)
    # Boundary provenance is kept on the revision itself so a report remains
    # independently auditable even when its exchange list is filtered or
    # archived separately.
    request_sha256: str | None = None
    response_sha256: str | None = None
    turn_id: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 0:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "candidate revision number is invalid")
        if not isinstance(self.action_id, str) or not self.action_id:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "candidate action_id is invalid")
        if self.action_kind not in {ActionKind.PROPOSE_MODEL.value, ActionKind.REQUEST_REVISION.value}:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "candidate action kind is invalid")
        for name, value in (
            ("candidate_sha256", self.candidate_sha256),
            ("parent_sha256", self.parent_sha256),
            ("prompt_sha256", self.prompt_sha256),
            ("context_sha256", self.context_sha256),
            ("gate_feedback_sha256", self.gate_feedback_sha256),
            ("request_sha256", self.request_sha256),
            ("response_sha256", self.response_sha256),
        ):
            if value is not None and (not isinstance(value, str) or not _DIGEST.fullmatch(value)):
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "%s is invalid" % name)
        _validate_generation(self.source_generation, "candidate source_generation")
        _validate_template_lock(self.template_lock, "candidate template_lock")
        for name, value in (("turn_id", self.turn_id), ("session_id", self.session_id)):
            if value is not None and (not isinstance(value, str) or not value or len(value) > 256):
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "candidate %s is invalid" % name)
        if self.gate_feedback is not None:
            if self.gate_feedback_sha256 not in (None, self.gate_feedback.sha256):
                raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "gate feedback digest does not match feedback")
            object.__setattr__(self, "gate_feedback_sha256", self.gate_feedback.sha256)
        if not isinstance(self.candidate_summary, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "candidate summary must be an object")
        _assert_no_forbidden_verdict(self.candidate_summary, "candidate_summary")
        object.__setattr__(self, "candidate_summary", freeze(_bounded(self.candidate_summary)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "action_id": self.action_id,
            "action_kind": self.action_kind,
            "candidate_sha256": self.candidate_sha256,
            "source_generation": self.source_generation,
            "template_lock": self.template_lock,
            "parent_sha256": self.parent_sha256,
            "prompt_sha256": self.prompt_sha256,
            "context_sha256": self.context_sha256,
            "gate_feedback_sha256": self.gate_feedback_sha256,
            "gate_feedback": None if self.gate_feedback is None else self.gate_feedback.to_dict(),
            "candidate_summary": thaw(self.candidate_summary),
            "request_sha256": self.request_sha256,
            "response_sha256": self.response_sha256,
            "turn_id": self.turn_id,
            "session_id": self.session_id,
        }


@dataclass(frozen=True)
class QualificationReport:
    """Secret-free, hash-addressed M2 qualification certificate."""

    provider: Mapping[str, Any]
    protocol_version: str
    runtime_identity: Mapping[str, Any]
    source_generation: str
    template_lock: str | None
    status: str
    exchanges: tuple[ProviderExchange, ...] = ()
    revisions: tuple[CandidateRevision, ...] = ()
    fallback_provider: Mapping[str, Any] | None = None
    errors: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    finished_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider, Mapping) or not isinstance(self.runtime_identity, Mapping):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification identity must be objects")
        if not isinstance(self.source_generation, str) or not self.source_generation:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification source_generation is required")
        if self.template_lock is not None and not isinstance(self.template_lock, str):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification template_lock is invalid")
        _validate_generation(self.source_generation, "qualification source_generation")
        _validate_template_lock(self.template_lock, "qualification template_lock")
        if self.status not in QUALIFICATION_STATUSES:
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification status is invalid")
        exchanges = tuple(self.exchanges)
        revisions = tuple(self.revisions)
        errors = tuple(self.errors)
        if any(not isinstance(item, ProviderExchange) for item in exchanges):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification exchanges are invalid")
        if any(not isinstance(item, CandidateRevision) for item in revisions):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification revisions are invalid")
        if any(not isinstance(item, Mapping) for item in errors):
            raise ProtocolError(ErrorCode.INVALID_ARGUMENTS, "qualification errors are invalid")
        object.__setattr__(self, "provider", freeze(_bounded(self.provider)))
        object.__setattr__(self, "runtime_identity", freeze(_bounded(self.runtime_identity)))
        object.__setattr__(self, "exchanges", exchanges)
        object.__setattr__(self, "revisions", revisions)
        object.__setattr__(self, "errors", tuple(freeze(_bounded(item)) for item in errors))
        object.__setattr__(self, "metadata", freeze(_bounded(self.metadata)))
        if self.fallback_provider is not None:
            object.__setattr__(self, "fallback_provider", freeze(_bounded(self.fallback_provider)))

    @property
    def passed(self) -> bool:
        return self.status == QUALIFICATION_PASS

    @property
    def report_sha256(self) -> str:
        return _digest(self._core_dict())

    def _core_dict(self) -> dict[str, Any]:
        return {
            "schema_version": QUALIFICATION_SCHEMA_VERSION,
            "provider": thaw(self.provider),
            "protocol_version": self.protocol_version,
            "runtime_identity": thaw(self.runtime_identity),
            "source_generation": self.source_generation,
            "template_lock": self.template_lock,
            "status": self.status,
            "exchanges": [item.to_dict() for item in self.exchanges],
            "revisions": [item.to_dict() for item in self.revisions],
            "fallback_provider": None if self.fallback_provider is None else thaw(self.fallback_provider),
            "errors": [thaw(item) for item in self.errors],
            "metadata": thaw(self.metadata),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    def to_dict(self) -> dict[str, Any]:
        value = self._core_dict()
        value["report_sha256"] = self.report_sha256
        return value


@dataclass(frozen=True)
class RevisionWorkflowResult:
    """Runtime result plus the M2 qualification certificate."""

    runtime: Any
    report: QualificationReport

    @property
    def status(self) -> str:
        return self.report.status

    def to_dict(self) -> dict[str, Any]:
        runtime_value = self.runtime.to_dict() if hasattr(self.runtime, "to_dict") else self.runtime
        return {"runtime": runtime_value, "qualification": self.report.to_dict()}

