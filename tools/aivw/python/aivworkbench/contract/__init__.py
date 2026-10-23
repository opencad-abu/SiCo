"""Bounded contract values: errors, redaction, JSON values and context snapshots.

One authority for freezing/redacting contract values, machine-readable errors,
artifact locators and bounded context pages.  The retiring agent stack
re-exports from here through temporary shims until the agent retirement batch
deletes them (see docs/AGENT_RETIREMENT_PLAN.md).
"""

from .artifact_locator import ArtifactLocator as ArtifactLocator
from .bounded_context import (
    BoundedContext as BoundedContext,
    MIN_CONTEXT_BYTES as MIN_CONTEXT_BYTES,
    PAGE_TRANSPORT_OVERHEAD_BYTES as PAGE_TRANSPORT_OVERHEAD_BYTES,
    build_context as build_context,
)
from .context_page import ContextPage as ContextPage
from .context_values import canonical_json as canonical_json
from .errors import (
    AgentError as AgentError,
    ErrorCode as ErrorCode,
    ProtocolError as ProtocolError,
)
from .json_values import ensure_json as ensure_json, freeze as freeze, thaw as thaw
from .redaction import (
    contains_secret_name as contains_secret_name,
    redact as redact,
    redact as redact_secrets,
    redact_text as redact_text,
)

__all__ = [
    "AgentError",
    "ArtifactLocator",
    "BoundedContext",
    "ContextPage",
    "ErrorCode",
    "MIN_CONTEXT_BYTES",
    "PAGE_TRANSPORT_OVERHEAD_BYTES",
    "ProtocolError",
    "build_context",
    "canonical_json",
    "contains_secret_name",
    "ensure_json",
    "freeze",
    "redact",
    "redact_secrets",
    "redact_text",
    "thaw",
]
