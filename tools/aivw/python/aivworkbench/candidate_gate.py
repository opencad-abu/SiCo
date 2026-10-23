"""Conservative structural contract gate for candidate model sources.

Rejects malformed, verdict-bearing or stale candidates before any EDA
execution.  Numerical quality stays with the deterministic metrics gates;
this gate is a safety boundary, not an analog-quality oracle.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .contract.errors import ProtocolError
from .gate_feedback import GateFeedback, _assert_no_forbidden_verdict


def default_candidate_gate(
    candidate: Mapping[str, Any],
    context: Mapping[str, Any],
) -> GateFeedback:
    """Reject malformed or stale candidates before behavior evaluation."""

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
