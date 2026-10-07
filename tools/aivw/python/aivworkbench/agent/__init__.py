"""Self-contained AIVW agent foundation.

The package deliberately exposes a small, versioned contract.  Providers may
propose actions, but all side effects are owned by :mod:`tool_broker` and are
checked by :mod:`policy` first.
"""

from .backend import (
    AgentProvider,
    ProviderEvent,
    ProviderRequest,
    ProviderResponse,
    ProviderSession,
    ProviderUnavailable,
)
from .protocol import (
    ACTION_KINDS,
    PROTOCOL_VERSION,
    Action,
    ActionKind,
    AgentError,
    Envelope,
    ErrorCode,
    Event,
    EventType,
    ProtocolError,
    decode_jsonl,
    encode_jsonl,
)
from .runtime import AgentRuntime, AgentState, RuntimeConfig, RuntimeResult
from .mcp import JSONRPC_VERSION, RpcRequest, RpcResponse, ToolRpcServer
from .runtime_info import (
    EXPECTED_PYTHON_VERSION,
    collect_runtime_info,
    detect_capabilities,
    validate_production_runtime,
)
from .qualification import (
    CandidateRevision,
    GateFeedback,
    ProviderExchange,
    QualificationReport,
    RevisionWorkflowResult,
    build_replay_records,
    configuration_validation_report,
    default_candidate_gate,
    qualify_bundle_provider,
    qualify_provider,
    qualify_replay_provider,
    qualify_with_fallback,
    run_candidate_revision_workflow,
    write_qualification_report,
)
from .providers.http_model import HttpModelConfig, SecretReference

__all__ = [
    "ACTION_KINDS",
    "PROTOCOL_VERSION",
    "Action",
    "ActionKind",
    "AgentError",
    "AgentProvider",
    "AgentRuntime",
    "AgentState",
    "Envelope",
    "ErrorCode",
    "Event",
    "EventType",
    "ProtocolError",
    "ProviderEvent",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderSession",
    "ProviderUnavailable",
    "RuntimeConfig",
    "RuntimeResult",
    "JSONRPC_VERSION",
    "RpcRequest",
    "RpcResponse",
    "ToolRpcServer",
    "EXPECTED_PYTHON_VERSION",
    "collect_runtime_info",
    "detect_capabilities",
    "validate_production_runtime",
    "CandidateRevision",
    "GateFeedback",
    "ProviderExchange",
    "QualificationReport",
    "RevisionWorkflowResult",
    "build_replay_records",
    "configuration_validation_report",
    "default_candidate_gate",
    "qualify_bundle_provider",
    "qualify_provider",
    "qualify_replay_provider",
    "qualify_with_fallback",
    "run_candidate_revision_workflow",
    "write_qualification_report",
    "SecretReference",
    "HttpModelConfig",
    "decode_jsonl",
    "encode_jsonl",
]
