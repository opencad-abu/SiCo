"""Compatibility exports for bounded agent context.

Owners live in artifact_locator, bounded_context, context_page, and redaction.
Remove this facade after external consumers migrate to those domain interfaces.
"""

from .artifact_locator import ArtifactLocator as ArtifactLocator
from .bounded_context import (
    BoundedContext as BoundedContext,
    MIN_CONTEXT_BYTES as MIN_CONTEXT_BYTES,
    PAGE_TRANSPORT_OVERHEAD_BYTES as PAGE_TRANSPORT_OVERHEAD_BYTES,
    build_context as build_context,
)
from .context_page import ContextPage as ContextPage
from .redaction import redact as redact_secrets


def redact_text(value: str) -> str:
    result = redact_secrets(value)
    return result if isinstance(result, str) else "[REDACTED]"


__all__ = [
    "ArtifactLocator",
    "BoundedContext",
    "ContextPage",
    "MIN_CONTEXT_BYTES",
    "PAGE_TRANSPORT_OVERHEAD_BYTES",
    "build_context",
    "redact_secrets",
    "redact_text",
]
