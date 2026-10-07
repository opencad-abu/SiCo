"""MCP operations over the server-owned verification candidate ledger."""

from .candidate_ledger import CandidateLedgerError
from .mcp_verification_schema import SUBMIT_CANDIDATE, GET_CANDIDATE

CANDIDATE_NAMES = frozenset(tool["name"] for tool in (SUBMIT_CANDIDATE, GET_CANDIDATE))


class CandidateHandlerArgumentError(ValueError):
    """Raised for invalid candidate requests or unavailable storage."""


def dispatch_candidate(name, arguments, *, get_ledger):
    if name not in CANDIDATE_NAMES:
        raise ValueError(f"unknown candidate tool: {name}")
    try:
        if name == "submit_candidate":
            return True, get_ledger().submit(arguments)
        include_source = arguments.get("include_source", False)
        if not isinstance(include_source, bool):
            raise CandidateLedgerError("include_source must be boolean")
        return True, get_ledger().get(
            arguments.get("candidate_sha256"), include_source=include_source,
        )
    except CandidateLedgerError as exc:
        raise CandidateHandlerArgumentError(str(exc)) from exc
